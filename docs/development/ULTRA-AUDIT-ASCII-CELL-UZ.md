# Ultra-audit — ASCII-cell invariant (`platform_runtime/cells.py`)



> **Qayta tiklandi (2026-09-21).** Bu bo'lim — I faza, §1–§10 — 2026-09-21 da
> hujjatga mundarija qo'shish skripti tomonidan **o'chirib yuborilgan** va
> `.workbuddy-ai/memory/2026-09-20.md` dagi o'lchov yozuvlaridan **qayta
> tiklandi**. Raqamlar, xulosalar va modul ro'yxati o'sha yozuvdan olingan;
> matn asli bilan so'zma-so'z bir xil **emas**. Tiklashda hujjatning o'z
> §147.6 dagi darsi takrorlandi: skript bilan tahrirlaganda **oraliqni
> tasdiqlamaslik** — bu yerda skript keyingi sarlavhagacha yurib, 242 satr
> haqiqiy matnni ham olib ketdi.

## 1. Qanday topildi (tasodif emas, o'lchov)

`inventory._number` ning chekka holatlari skani paytida `_number('१२')`
(devanagari) → **`12.0`** chiqdi. Savol tug'ildi: bu xato? O'lchandi.

**Ildiz sabab:** Python'da `re` **standart holatda Unicode-aware**, ya'ni `\d`
faqat `[0-9]` emas — **`Nd` (decimal digit) kategoriyasining hammasi**. Repo'da
`re.ASCII` **hech qayerda** ishlatilmaydi (tekshirildi). `float()` ham Unicode
raqamlarni **normalizatsiya qiladi**.

**O'lchangan ta'sir — 4 modul, hammasi jimgina:**

| Modul | Kirish | Natija | Nega muhim |
|---|---|---|---|
| `erp._date_text` | `'१३.01.2026'` | **`'2026-01-13'`** | Sanani **jimgina qayta yozadi** — tizimda umuman bo'lmagan ASCII qator qaytadi. Bu *"platforma sanani to'qimaydi"* va'dasiga zid; noto'g'ri davrga o'tkazilgan hujjat — **soliq muammosi**. |
| `erp._date_text` | `'२०२६-01-02'` | qabul | ISO shoxobchasi; dublikat-ledger kaliti shu qatordan |
| `documents.py` 564 | `'२०२६-01-03'` | weekend signal | `int(part)` → `date(2026,1,3)` **shanba**. O'lchandi: `documents` **2026-01-02 ni bir marta ham ishlatgan**, ya'ni signal *o'sha* sanaga tushadi. |
| `escalation._is_stale` | `'२०२०-01-01'` | `AGEABLE` | `int(group)` → sana **yaroqli** deb hisoblanadi. Modul va'dasi: *"o'qib bo'lmaydigan sana **hech qachon** eskirmaydi"* — buzildi. |
| `inventory`/`oee`/`manufacturing` `._number` | `'１２'` | `12.0` | o'lchovli miqdor/narx |

## 2. Nima uchun bu "kosmetik" emas

`erp.py` — repo'dagi **eng xavfli kod** (moliyaviy yozuv). Uning docstring'i
*"hech qanday maydon **to'qilmaydi**"* deb va'da qiladi; `'१३.01.2026'` →
`'2026-01-13'` esa **aynan to'qish** (qayta yozish).

Uchta mustaqil shoxobcha (`erp`, `documents`, `escalation`) bitta kutilmagan
kirishga **uch xil** javob beradi — bu tasodif emas, **umumiy o'zgarmasning
yo'qligi**.

## 3. O'lchangan to'liq qamrov — 13 modul

Uch skan, har biri oldingisidan kengroq:

1. **Modul bo'yicha** — 4 modul.
2. **Naqsh bo'yicha** — yana 4 modul (`workforce`, `telephony`, `vision`,
   `tools`). Ya'ni birinchi skan **yetarli emas edi**. Nuqson modulning ishi
   emas — **hammasining umumiy odati**.
3. **Konversiya bo'yicha** — `whatsapp_inbound._epoch`: `str.isdigit()` ham
   **Unicode-aware**. `'१२३'` → `int('१२३')` = `123` → oyna **1970** dan
   hisoblanardi.

**Tekshirilgan va TOZA topilgan (o'lchangan, taxmin emas):**

* **`business_graph.canonical`** — `_NUMERIC` **har doim** `[0-9]` ishlatgan,
  ya'ni ziddiyat detektori **allaqachon to'g'ri** edi. Eng nozik joy: Unicode
  raqam na raqam, na ASCII egizakning ikkinchi yozilishi bo'lishi kerak — aks
  holda buzilgan katak haqiqiy qiymat bilan *kelishgan* deb ko'rsatilardi (bu
  xabar qilingan ziddiyatdan **yomonroq**). Probe xossasi qo'shildi.
* `database/*` (`validate_endpoint`), `sheets.bounded_int` (operator konfigi),
  `crm_contract.bounded_price` (string qabul qilmaydi) — katak o'qimaydi.

**Yakuniy qamrov: 13 modul, 11 xil yo'l.**

## 4. Ikkinchi, alohida nuqson — `workforce._hours`

P12/P13 auditi `inf`/`nan` teshigini `manufacturing._number` va `oee._number` da
yopdi, lekin **`workforce` ga yetib bormadi** — o'sha skan ham *modul bo'yicha*
yurilgan edi. Bu safar u **umumiy o'zgarmas** sifatida yozildi.

## 5. Uchinchi nuqson — o'lik konstantalar

`inventory.MAX_NAMES` va `oee.MAX_NAMES` — e'lon qilingan, **hech qayerda
o'qilmaydigan**. Oilada `MAX_NAMES` *odamlar ro'yxatini* chegaralaydi;
`inventory` mahsulot, `oee` stansiya qaytaradi — chegaralanadigan `names` yo'q.

Ishlatilmaydigan chegara **yo'q chegaradan yomonroq**. Ikkalasi izoh bilan olib
tashlandi.

## 6. Tuzatish — bitta o'zgarmas, ikki sinfda

**Yangi umumiy modul:** `api-python/platform_runtime/cells.py`. Ikki funksiya,
**ataylab boshqacha**:

* `is_ascii_number(text)` — qiymat o'quvchilar uchun ("raqam sifatida o'qish
  mumkinmi?"). `-5`, `1,5` qabul; `\d` oilasi, `1e5`, bo'sh, non-string rad.
* `is_ascii_digit_run(text)` — sana/qiymat parserlari uchun ("parsing
  mumkinmi?"). `int()` **normalizatsiya qilishidan oldin** rad etish kerak.

Ajratish **funksional zarur**: birinchisi belgi/vergulni qabul qiladi, ikkinchisi
faqat toza raqamlar ketma-ketligini.

**Tuzatilgan 11 modul:** `erp`, `documents` (ikki joy + hafta kuni yo'li),
`escalation`, `workforce` (`_hours` + `_iso_day_text`), `telephony`
(`_duration` + `DAY_RE`), `vision` (`DAY_RE`), `inventory`, `oee`,
`manufacturing`, `tools` (Instagram `account_id`).

## 7. O'lchovlar

| Nima | Oldin | Hozir |
|---|---|---|
| Offline to'plam | 2229 | **2276** |
| Signatura | `failures=1, errors=149, skipped=1` | **aynan o'zgarmadi** |
| `test_cells.py` | — | **45, OK** |
| `probe_inventory_boundary` | 68 xossa | **128 xossa** (+60, §7) |
| Ta'sirlangan suite'lar | — | **642 OK** |
| Config checker'lar | 7 | **7 OK** |
| Boshqa probe'lar | 4 | **4 PROVEN** |
| Registry | 88 | **88** (o'zgarmadi) |

**Aritmetika aniq:** 2229 + 2 (`test_business_graph.py` da avvalgi qadamning 2 ta
`graph.entities` testi) + 45 (`test_cells`) = **2276**. Izohlanmagan farq yo'q
(avval 2268 chiqqan edi — farq shu 2 ta testdan, topildi).

**17-marta** ketma-ket signatura o'zgarmadi. Yagona `failures=1` — Windows-only
`test_all_files_private` (`0 != 63`).

### Qo'riqchilar (ikki qavat)

1. **Xulq-atvor:** `api-python/runtime_tests/test_cells.py` — har bir modul
   **tashqarisidan** rad etishni o'lchaydi.
2. **Struktura:** `NoUnicodeDigitPatternRemainsTests` — **11 kuzatilayotgan
   modulning manba matnini** o'qiydi, yalang'och `\d` qaytsa **yiqiladi**.

**Qo'riqchi diskriminatsiya qilishini o'lchadim:** `oee.NUMBER_RE` ni buzib
qaytardim → **4 test yiqildi** (`12.0 is not None`). Tikladim → `OK`. Ya'ni
test haqiqatan o'lchaydi.

### Test xatosi (mening, o'lchov tomonida)

`telephony._duration('12,5')` → `12.5` deb kutgandim, aslida `None`. Sabab:
vergul **naqshdan oldin** nuqtaga almashtiriladi, keyin `[0-9]{1,6}` uni rad
etadi. Bu **mening tuzatishimdan oldin ham** shunday edi — regressiya emas.
Test **o'lchangan holat** sifatida qayta yozildi (`12.5` qabul, `12,5` rad).

## 8. Nima **qilmadi** (ataylab)

* `business_graph.canonical` ga **tegilmadi** — u allaqachon to'g'ri edi, va
  o'lchov buni tasdiqladi. To'g'ri kodni "bir xillashtirish" uchun o'zgartirish
  xossasiz o'zgarish bo'lardi.
* `database/*`, `sheets.bounded_int`, `crm_contract.bounded_price` ga
  **tegilmadi** — ular katak o'qimaydi, ya'ni invariant ularga tegishli emas.
* Umumiy `re.ASCII` ni **global qilib qo'yilmadi** — bu butun repo bo'ylab
  xatti-harakatni o'zgartirardi va o'lchanmagan ta'sir keltirardi. O'rniga
  **nomlangan ikki funksiya** qo'shildi, ya'ni har bir chaqiruv joyi o'z
  shartnomasini **tanlaydi**.

## 9. Saboq (uch tomonlama)

1. **"Docstring — dalil emas"** — 17-marta. Bu safar aksi: kod **va'da qilgan**
   narsani (`erp` "to'qimaydi") kod **buzayotgan** edi.
2. **Modul bo'yicha skan yetarli emas — naqsh bo'yicha skanerlash kerak.**
   Birinchi skan 4 modul topdi, ikkinchisi yana 4 tasini.
3. **Umumiy o'zgarmas umumiy modulda yashashi kerak.** Alohida tuzatilgan qoida
   qo'shni modulga yetmaydi.

## 10. Ochiq qolgan

Ultra-audit davom etadi: `_reason()` barcha siyosatlarda, `limit`/truncation
o'zaro ta'siri, `assets`/`briefing`/`supervisor` kesishmalari. Bu ishlar
keyingi fazalarda bajarildi — II fazadan boshlab quyida.

# II faza — `truncated` haqiqatni aytmaydi

**Bu faza boshqa nuqson sinfini topdi.** ASCII skani qiymat **o'qishdan** keyin
bo'ldi; bu skan **natija shaklidan** keyin bo'ldi. Ikkalasi ham bir xil oilaga
tegishli: **jimgina ishonchli noto'g'ri signal**.

## 11. Nuqson: `len(x) >= limit` "to'liq" ni "to'silgan" deb o'qiydi

`truncated` — operatorga "yana bor, toraytir" deb aytadigan bayroq. U shunday
hisoblanardi:

```python
'truncated': len(found) >= limit
```

Bu ifoda **ikki xil holatda ham rost**: ro'yxat haqiqatan kesilganda **va**
ro'yxat aynan `limit` da tugaganda. Ular **qarama-qarshi** ma'noni bildiradi:

- `True` → "yana ma'lumot bor, kattaroq limit bilan qayta so'ra"
- `False` → "bu hammasi"

Birinchi javob **ikkinchisi rost bo'lganda** berilsa, operator **bir xil ro'yxatni
qaytaradigan** so'rovni, cheksiz, qayta yuboradi.

**Yo'qotilgan ma'lumot yo'q** — lekin **yagona to'g'ri signal** (to'liqlik)
yo'qoladi. Bu "jimgina ishonchli noto'g'ri javob" sinfining aynan o'zi.

## 12. O'lchandi, taxmin qilinmadi

Probe'dan **tuzatishdan oldin**:

```
rows= 10 limit= 10 | direct: n= 10 truncated=True  (more: False) WRONG
rows=  3 limit=  3 | direct: n=  3 truncated=True  (more: False) WRONG
rows= 10 limit= 10 | collected: n= 10 truncated=True (more: False) WRONG
```

`graph.search` orqali, **haqiqiy tool**, **haqiqiy config** bilan:

```
rows= 10 matching= 10 limit= 10 | returned= 10 truncated=True (more: False) WRONG
rows=  3 matching=  3 limit=  3 | returned=  3 truncated=True (more: False) WRONG
```

Ya'ni **aynan `limit` ta natija** — kesilmagan — `truncated=True` deb
xabar qilinardi.

## 13. To'g'ri predikat: rad etish **paytida** yozib olish

Kesilganini bilishning yagona dalili — **yana bitta element bor edi va limit uni
rad etdi**. Shuning uchun bayroq **rad etish joyida** o'rnatiladi, keyin uzunlikdan
**o'qib olinmaydi**:

```python
if len(matches) >= limit:
    cut = True      # yana bitta mos keldi va limit uni rad etdi
    break
```

`graph.search` da skan **to'lgan ro'yxatdan keyin ham davom etadi** — aks holda
"kesildi" ni bilishning yo'li yo'q. `by_id` allaqachon xotirada, shuning uchun
qo'shimcha ish `MAX_MATCHES` bilan chegaralangan va **qo'shimcha provider
o'qishi yo'q**.

## 14. Topilgan to'rt joy (naqsh bo'yicha skan)

Bu safar skan **predikat** bo'yicha bo'ldi, modul bo'yicha emas — va shu sabab
**`business_graph` tashqarisidagi** joylar ham chiqdi:

| Modul | Joy | Tuzatish |
|---|---|---|
| `business_graph` | `identifiers()` ikki yo'l | bayroq rad etishda |
| `business_graph` | `search()` | bayroq rad etishda; skan to'liqdan keyin davom etadi |
| `business_graph` | `conflicts()` | bayroq rad etishda |
| `business_graph` | `timeline()` | bayroq rad etishda |
| `oversight` | `activity()` — SQL `LIMIT ?` | **`limit + 1`** so'raladi, ortiqchasi tashlanadi |
| `workforce` | `shifts()` | bayroq rad etishda |
| `workforce` | `workload()` | kesish — `[:limit]` slice; solishtirish slice'dan **oldin** |

`oversight` boshqacha: uzunlikdan **umuman** bilib bo'lmaydi, chunki SQL o'zi
`limit` da kesadi. Yagona halol yo'l — **bittasini ortiq so'rash**. Shunday
qilindi.

**To'g'ri bo'lgan joylar tegmadi:** `inventory` (`>=` **va** `len(ids) > len(out)`
— ikkinchi shart uni to'g'ri qiladi), `assets`, `sheets`, `manufacturing`, `oee`,
`telephony`, `vision`, `workforce.attendance` — bular `>` ishlatadi yoki
`raw.get('truncated')` ni ko'taradi.

## 15. Test **nuqsonni yozib qo'ygan** edi

`test_oversight.test_activity_truncation_is_reported` **`limit=1` → `True`** deb
da'vo qilardi. Seed'da bu agent uchun **aynan 1 ta** audit qatori bor — ya'ni
javob **to'liq**, `False` bo'lishi kerak.

Eski predikat (`len(audit) >= limit` → `1 >= 1`) **tasodifan** `True` bergan, va
test **o'tib ketgan**. Ya'ni test bor edi, yashil edi, lekin **noto'g'ri javobni
qulflab qo'ygan edi**.

Tuzatish buni ochdi. Test **to'g'ri kutishga** o'zgartirildi va yoniga **haqiqiy
kesish** holati qo'shildi — bayroq **harakatlanishi** ko'rinishi uchun.

## 16. Qo'riqchilar diskriminatsiya qiladi

- `business_graph` predikatlarini eski holiga qaytardim → probe'da **2 FAIL**,
  suite'da **3 failures, 15 errors** (`cut` o'zgaruvchisi yo'q — ya'ni u
  **haqiqatan yuk ko'taradi**). Tikladim → `OK`.
- Probe **oldin ham** nuqsonni tutgan (`truncated=True (more: False) WRONG`) va
  **keyin ham** tutadi. O'lchov asbobi, bezak emas.

## 17. O'lchovlar (II faza)

| Nima | Oldin | Hozir |
|---|---|---|
| Offline to'plam | 2276 | **2334** |
| Signatura | `failures=1, errors=149, skipped=1` | **aynan o'zgarmadi** |
| `test_business_graph` | 165 | **168** (+3) |
| `test_workforce` | 32 | **36** (+4) |
| `test_oversight` | 33 | **34** (+1) |
| `test_inventory` | 38 | **82** (+44, I fazadan) |
| `probe_truncation_verdict` | — | **32 xossa, 32 pass, exit 0** |
| Ta'sirlangan suite'lar | — | 664 test **OK** |
| Registry | 88 / 89 | **88 / 89** (o'zgarmadi) |
| Config checker'lar | 7 | **7 OK** |
| Boshqa probe'lar | 4 | **4 PROVEN** |

**Aritmetika — halol yozib qo'yilishi kerak.** `2276` da'vosi I faza oxirida
yozilgan edi, lekin u **o'sha fazada `test_inventory` 38 → 82 ga o'sganini
qo'shmagan** (+44). Ya'ni `2276 + 44 + 8 = 2328`, o'lchangan esa **2334**.

Farq yopildi va u **o'lchov asbobidagi** nuqson edi, suite'dagi emas:

- Modul-modul yig'indi (`unittest runtime_tests.<mod>`) = **2201**.
- Bitta `discover` o'tishi = **2334**.
- Farq **133**: besh modul (`test_google_adapters` 21, `test_google_reconcile` 28,
  `test_google_sync` 43, `test_managed_graph_search` 45, `test_portable_fs` 1)
  **yakka holda import bo'lmaydi** — ular umumiy helper'ni faqat discovery
  kontekstida topadi. Shuning uchun yakka holda **0**, discovery ostida **to'liq**
  hissa qo'shadi.

Ya'ni **2334 ta testning hammasi haqiqatan ishlaydi**; ikkala raqam ham to'g'ri,
ular **ikki xil savolga** javob beradi. `2334` — suite haqida; `2201` — modul
haqida. Da'vo uchun **`2334`** ishlatiladi, chunki runner shuni o'lchaydi.

**Muhim sinov intizomi:** `2334` — **o'lchangan** run natijasi (bitta
`discover`, `Ran 2334 tests`). Waypoint arifmetikasi **tekshiruv** uchun, da'vo
uchun emas — va u aytsa ham, **o'lchov ustun turadi**.

Yagona `failures=1` — **Windows-only** `test_all_files_private` (`0 != 63`,
POSIX mode bitlari Windows'da yo'q), oldindan mavjud va o'zgarmagan.

## 18. Saboq (II faza)

1. **Predikat — bu savol.** `>=` va `>` bu yerda bir xil ko'rinadi, lekin biri
   **yolg'on gapiradi**. Nuqson modulda emas, **ifodada** edi.
2. **Naqsh bo'yicha skan modul bo'yicha skandan kuchliroq** — bu **ikkinchi marta**
   tasdiqlandi. Birinchi marta `\d` edi, bu safar `>= limit` edi.
3. **Yashil test dalil emas.** Test bor edi va o'tardi, lekin **nuqsonni
   qulflagan** edi. O'lchovsiz test — bu nazorat emas, **taxmin**.
4. **Bir xil signal ikki xil ma'no bersa, u signal emas.** `truncated` shu holga
   tushgan edi; endi u bitta savolga bitta javob beradi.

## 19. II faza — ochiq qolgan

- `search()` endi to'liq ro'yxatdan keyin ham skan qiladi (`by_id` xotirada).
  Bu **ataylab**: `MAX_MATCHES` (100) bilan chegaralangan, qo'shimcha o'qish yo'q.
  Agar kelajakda `MAX_MATCHES` sezilarli oshsa, qayta ko'rib chiqilishi kerak.
- `timeline()` `order: 'provider'` deb e'lon qiladi va ro'yxat **manba bo'yicha**
  tartiblangan (1-manbaning hammasi, keyin 2-manba). Bu **docstring'da ochiq**
  yozilgan ("xronologik deb da'vo qilinmaydi"), shuning uchun nuqson emas — lekin
  `limit` to'lsa **keyingi manbalar umuman skanerlanmaydi**, va bu faqat
  `truncated=True` orqali ko'rinadi. Endi u **haqiqatni** aytadi.

---

# III faza — `count` inventar o'rniga sahifa o'lchamini aytadi

## 20. Nuqson: `erp.posting_status` "nechta posting bor" ga "nechta sig'di" deb javob berardi

```python
rows = ledger(engine, tenant, limit, ...)
return {..., 'postings': rows, 'count': len(rows)}
```

`count` — bu **qaytarilgan** qatorlar soni, **jami** emas. Va `truncated` kaliti
**umuman yo'q**. O'lchandi (`erp.posting_status`, haqiqiy tool + config):

```
rows=   5 limit=  20 -> count=   5
rows=  20 limit=  20 -> count=  20
rows=  21 limit=  20 -> count=  20    ← 21 ta bor, lekin "20" deyiladi
rows= 100 limit=  20 -> count=  20    ← 100 ta bor, lekin "20" deyiladi
```

Ya'ni **20 ta posting** bilan **100 ta posting** **bir xil** javob beradi. Bu
`truncated` nuqsonidan **bir qadam narida**: u yerda bayroq **yolg'on** edi, bu
yerda bayroq **yo'q** va son **jami** deb o'qiladi. Bu shunchaki chegara emas —
**inventar noto'g'ri aytilishi**.

## 21. Tuzatish: `ledger_page()` — davr va populyatsiya bir o'qishda

```python
def ledger_page(engine, tenant, limit=50, document=''):
    """Returns (rows, total, truncated)."""
    ...
    total = cursor.execute(
        f'SELECT count(*) n FROM p_erp_postings WHERE {where}', params).fetchone()['n']
    ...
    return rows, total, total > len(rows)
```

Tool endi:

```python
'postings': rows, 'count': total, 'returned': len(rows), 'truncated': truncated
```

Uchta **alohida** fakt: `count` = populyatsiya, `returned` = sahifa,
`truncated` = biror narsa qoldimi. `ledger()` ning **ro'yxat shartnomasi
o'zgarmadi** — uni boshqa chaqiruvchilar ishlatadi.

**Predikat yana populyatsiyaga nisbatan:** `total > len(rows)`. Aynan `limit` ta
qatorli daftar **to'liq** deb hisoblanadi, kesilgan deb emas.

## 22. Test ham nuqsonni o'lchamagan edi

`test_status_reads_the_ledger_and_reports_the_driver` va
`test_status_limit_is_coerced_not_typed` — ikkalasi ham **bo'sh** daftarda
`count == 0` ni tekshiradi, u yerda eski va yangi **bir xil** javob beradi.
Shuning uchun nuqson ko'rinmagan. Ikkita yangi test qo'shildi (`test_erp.py`
54 → **56**): 25 posting + `limit=10` → `count=25, returned=10, truncated=True`;
va 10 posting + `limit=10` → `count=10, truncated=False`.

**Qo'riqchi diskriminatsiyasi tekshirildi:** eski shaklga qaytarildi →
`AssertionError: 25 != 10`; tiklandi → OK.

## 23. Bir xil naqshning qolgan joylari — ataylab **tegilmadi**, lekin belgilandi

`briefing.ledger()`, `escalation.ledger()`, `reengagement.ledger()`,
`supervisor.history()` — **bir xil shakl** (SQL `LIMIT ?`, yalang'och ro'yxat).
Tekshirildi: **runtime'da hech kim ularni chaqirmaydi** (faqat testlar). Ya'ni
**hozircha tirik nuqson emas — tuzoq**. Birinchi bo'lib tool ulagan kishi
meros qilib oladi.

Shuning uchun ularning **docstring'iga** ogohlantirish yozildi (tuzatish emas):
"bu yalang'och ro'yxat qaytaradi, to'liq sahifa bilan butun daftar bir xil
ko'rinadi; `erp.posting_status` aynan shu nuqson edi". Bu loyihaning o'z
saboqiga mos: **"o'lik chegara chegarasizlikdan yomonroq"** — va bu yerda
chegara tirik edi, faqat **jimgina**.

## 24. `assets` — bu naqsh **to'g'ri** bo'lgan yagona modul

Tekshirildi va **nuqson yo'q**:

```python
'children': ordered[:limit], 'count': len(ordered),        # count — slice'DAN OLDIN
'count': seen, 'returned': len(out), 'truncated': seen > limit,   # seen — to'liq populyatsiya
```

`count` **kesishdan oldingi** ro'yxatdan olinadi. Ya'ni `assets` boshidanoq
shunday yozilgan edi — bu **namuna**, qolganlar shunga qarab tuzatildi.

## 25. O'lchovlar (III faza)

| Nima | Oldin | Hozir |
|---|---|---|
| Offline to'plam | 2334 | **2336** |
| Signatura | `failures=1, errors=149, skipped=1` | **aynan o'zgarmadi** |
| `test_erp` | 54 | **56** (+2) |
| `probe_truncation_verdict` | 17 xossa | **32 xossa** (6 bo'lim) |
| Ta'sirlangan suite'lar | — | 664 test **OK** |
| `test_briefing`+`escalation`+`reengagement`+`supervisor` | — | 232 test **OK** |
| Registry | 88 / 89 | **88 / 89** (o'zgarmadi) |

## 26. Saboq (III faza)

1. **"Bayroq yo'q" — "bayroq yolg'on" dan kam emas.** `truncated` yolg'on
   gapirardi; `count` esa **jami** deb o'qiladigan **sahifa o'lchami** edi.
   Ikkalasi ham operatorni **noto'g'ri qarorga** olib keladi.
2. **Bir xil nom ikki xil ma'no bersa — nom yolg'on.** `count` "nechta bor"
   degan savolga javob bergandek ko'rinadi. Endi uch nom bor:
   `count` (jami), `returned` (sahifa), `truncated` (qoldimi).
3. **Test bo'sh holatda yozilsa, nuqsonni ko'rmaydi.** Ikkala mavjud test
   `count == 0` ni tekshirardi — bo'sh daftarda farq yo'q. Nuqson faqat
   **chegaradan oshgan** holatda ko'rinadi.
4. **Tirik nuqson bilan tuzoqni ajratish kerak.** To'rt joy "bir xil shakl" edi;
   faqat **bittasi** operatorga ochiq edi. Qolganlari **hujjatlashtirildi**,
   tuzatilmadi — o'zgarish yuzasi o'zgarish **ehtiyoji** bilan teng bo'lishi kerak.


---

# IV FAZA — `documents.fraud_signals` arifmetikasi (2026-09-20)

## 27. Nima o'lchandi

Ikki shubha, ikkalasi ham **haqiqiy nuqson** bo'lib chiqdi. Ikkalasi ham
`fraud_signals` ning bitta `else` shoxobchasida.

### 27.1 "Mediana" mediana emas

```python
median = amounts[len(amounts) // 2]
```

Bu **yuqori-o'rta** element. Juft sonda mediana **emas**:

| Tarix | Chop etilgan | Haqiqiy mediana |
|---|---|---|
| `[100, 200]` | 200 | **150** |
| `[100, 300]` | 300 | **200** |
| `[100, 200, 300, 400]` | 300 | **250** |

**Xato yo'nalishi muhim:** har doim **yuqoriga**. Yuqoriroq mediana → yuqoriroq
chegara → bayroq **kamroq** ko'tariladi. Ya'ni nuqson signalni **zaiflashtiradi**,
va buni hech qayerda aytmaydi.

### 27.2 Hujjat o'z populyatsiyasida

```python
prior = [row for row in history if row['currency'] == doc['currency']]
```

Hujjat oqimi **receive → store → check**. Ya'ni tekshiruv vaqtida hujjat
**allaqachon saqlangan** va `supplier_history` uni qaytaradi. O'lchandi — bir xil
hujjat, bir xil summa:

| Holat | `history_considered` |
|---|---|
| Saqlashdan oldin tekshirilgan | 2 |
| **Saqlangandan keyin (normal oqim)** | **3** |

Ekstremal summa o'z chegarasini o'zi ko'taradi: **qanchalik ekstremal bo'lsa,
bayroq shunchalik qiyin ko'tariladi.**

## 28. Oqibat — asosiy o'lchov

Bu **kosmetik emas**. `posting_plan` (700-satr) material signalni
`needs_attention` ga ko'taradi:

```python
material = bool(fraud['material'])
needs_attention = bool(dup['duplicate'] or matched['status'] != 'matched'
                       or material)
...
elif material:
    recommendation = 'review_fraud_signals'
else:
    recommendation = 'ready_for_approval'
```

Haqiqiy `document.posting_plan` tool'i orqali o'lchandi, tarix
`[100000, 300000]` → haqiqiy mediana 200 000, 3x = **600 000**:

| Invoice | Eski kod | Yangi kod |
|---|---|---|
| 500 000 | ready_for_approval | ready_for_approval |
| **600 000** (3.0x) | **ready_for_approval** ❌ | review_fraud_signals ✅ |
| **700 000** (3.5x) | **ready_for_approval** ❌ | review_fraud_signals ✅ |
| **800 000** (4.0x) | **ready_for_approval** ❌ | review_fraud_signals ✅ |
| **899 999** (4.5x) | **ready_for_approval** ❌ | review_fraud_signals ✅ |
| 900 000 (4.5x) | review_fraud_signals | review_fraud_signals |

**600 000 – 899 999 oralig'i butunlay ko'r edi.** Bu haqiqiy 3x–4.5x mediana
bo'lgan invoice'lar — fraud tekshiruvi aynan shular uchun mavjud.

## 29. Tuzatish

```python
def _median(values):
    import statistics
    return int(statistics.median(values))
```

- `_median()` — `statistics.median`, butun minor birlikka yaxlitlangan
- `prior` endi `document_key` bo'yicha **o'z-o'zini chiqarib tashlaydi**
- `history_rows` (do'kon qaytargani) va `history_considered` (test solishtirgani) —
  **ikki xil nom, ikki xil kattalik**

**Nima uchun nom ikkiga bo'lindi:** normal oqimda bu ikki son **birga teng emas**
(biri ikkinchisidan bitta katta). Faqat birinchisini ko'rsatish o'quvchiga
populyatsiya aslida bo'lganidan kattaroq deb o'ylashga yo'l qo'yardi. Bu —
III fazadagi `count` / `returned` saboqi bilan **bir xil**.

## 30. Testlar

`test_documents` **83 → 90** (+7).

**Muhim topilma:** eski `test_an_outlier_signal_carries_its_evidence` **tasodifan**
yashil edi. Fixture 3 ta hujjat (toq son) → upper-middle = haqiqiy mediana.
Test `'1100000'` ni tekshirardi va **to'g'ri** chiqardi — lekin sabab kod emas,
**fixture** edi. Bundan tashqari test `count=3` ni da'vo qilardi, holbuki haqiqiy
oqimda hujjat saqlangan bo'lardi va `count=4` bo'lardi.

Yangi testlar **juft sondan** o'lchaydi va chegarani **ikkala tomondan** tekshiradi.

**Qo'riqchi tekshirildi:** tuzatish qaytarilganda → **3 failures, 1 error**.

## 31. Probe

`probe_truncation_verdict` **32 → 48 xossa** (7-bo'lim qo'shildi), **48 pass**,
exit 0.

**Probe'ning o'zida topilgan nuqson:** `record()` yordamchisi `is` ishlatardi,
`==` emas. Ilgari **hamma** bo'limlar boolean solishtirardi — boolean'lar
singleton, shuning uchun `is` to'g'ri ishlardi va xato **yashirin** qoldi. Birinchi
marta **hisoblangan butun son** solishtirilganda (`150000` — CPython keshidan
tashqarida) soxta **4 FAIL** berdi. `==` ga tuzatildi.

**Saboq:** o'lchov asbobini ham tekshirish kerak. Probe nuqson yo'q joyda nuqson
ko'rsatsa, u nuqsonni o'tkazib yuborgandek zararli.

## 32. To'plam holati

```
Ran 2341 tests in 273.344s
FAILED (failures=1, errors=149, skipped=1)
```

**Signatura aynan o'zgarmadi** (I, II, III faza bilan bir xil). Yagona xato —
Windows-only `test_all_files_private` (`AssertionError: 0 != 63`), oldindan mavjud.

`test_documents` **90 OK**. Probe **48/48**.

## 33. IV faza saboqlari

1. **"Median" deb chop etilgan son mediana bo'lishi shart.** Aks holda approver
   bayroqni tekshira olmaydi — u ko'rgan son bilan qaror asosini bog'lay olmaydi.
2. **Xatoning yo'nalishi muhim.** Yuqoriga og'ish = signalni zaiflashtirish.
   Zaif signal — nuqson, chunki u **jimgina** ishlamaydi (na xato, na log).
3. **"Populyatsiya" — bu e'lon qilinishi kerak bo'lgan tanlov.** O'z-o'zini
   kiritish **arifmetik** emas, **semantik** xato: savol "mediana qancha" emas,
   "nimaga nisbatan mediana".
4. **Probe'da ham nuqson bo'ladi.** `is` vs `==` — bitta belgi, 4 soxta FAIL.

---

# V FAZA — `documents.fraud_signals` birlik xatosi (2026-09-20)

## 34. Nima o'lchandi

IV faza `fraud_signals` ning `else` shoxobchasini ochdi. V faza **o'sha
shoxobchaning qolgan ikki signalini** tekshirdi va uchinchisida nuqson topdi.

### 34.1 `round_number` — noto'g'ri birlikda so'ralgan savol

```python
if doc['total_minor'] > 0 and doc['total_minor'] % (10 ** MINOR_UNITS[doc['currency']]) == 0:
    major = doc['total_minor'] // MINOR_UNITS[doc['currency']]
    digits = len(str(major).rstrip('0'))
```

Shart "summa **butun major birlik**mi?" deb so'raydi. `MINOR_UNITS`:
`UZS: 1`, `USD/EUR/RUB: 100`.

| Valyuta | Faktor | Shart aslida so'raydi | Ma'nosi |
|---|---|---|---|
| UZS | 1 | `minor % 10 == 0` | dumaloqlikka **yaqin** |
| USD | 100 | `minor % 100 == 0` | "sentlar nolmi?" |

**USD uchun "sentlar nolmi" deyarli har bir invoice'da rost.** Ya'ni signal
**har qanday valyutada o'lik**, faqat UZS'dan tashqari.

**Modul docstring aynan teskari da'vo qilardi:**
> "Computed in minor units so the test means the same thing for UZS (no subunit)
> and for USD (two digits), which a major-unit test would not."

O'lchandi — haqiqiy tool orqali:

| Valyuta | Summa | Dumaloqmi? | Bayroq |
|---|---|---|---|
| USD | 1 000.00 | **ha, mukammal** | **yo'q** ❌ |
| USD | 1 000.50 | yo'q | yo'q |
| UZS | 1 000 000 | ha | ha |
| UZS | 100 050 | **yo'q** | **ha** ❌ |

Ikki xato bir vaqtda: **dumaloq USD invoice'ni o'tkazib yuboradi** va
**dumaloq bo'lmagan UZS summani bayroqlaydi**. Docstring aytgan xususiyat
**teskarisi** edi.

### 34.2 `digits` — o'qiladigan son gapga mos kelmaydi

`len(str(major).rstrip('0'))` — bu **boshidagi muhim raqamlar soni**, lekin matn
uni **dumaloqlik** da'vosi sifatida chiqaradi:

| Summa | Chop etilgan | Matn | Haqiqat |
|---|---|---|---|
| UZS 100050 | **5** | "very round" | umuman dumaloq emas |
| UZS 1234000 | 4 | "round" | o'rtacha |
| UZS 1000000 | **1** | "barely round" | **eng dumaloq summa** |

Eng dumaloq summa eng **kichik** son oladi — mutlaqo teskari.

## 35. Tuzatish

**Birlik:** savol **major birlikda** so'raladi, chunki "dumaloq" — major
tushuncha:

```python
factor = MINOR_UNITS[doc['currency']]
if doc['total_minor'] > 0 and doc['total_minor'] % factor == 0:
```

**Son:** `trailing_zeros` — **oxiridagi nollar** soni, ya'ni gap aytgan narsa:

```python
text = str(major)
trailing = len(text) - len(text.rstrip('0'))
```

Endi: `UZS 1000000` → **6** (eng dumaloq, eng katta son), `UZS 100050` → **1**.

## 36. Yangi e'lon qilingan chegara — `ROUND_TRAILING_ZEROS = 3`

Birlikni tuzatgandan keyin **yangi savol** ochildi: 1 ta oxirgi nol
bayroqlanishi kerakmi?

O'lchandi: `123450`, `123460` — UZS'da eng kichik birlik 1 so'm, shuning uchun
bunday summalar **butunlay oddiy**. Chegara 1 bo'lsa signal **har uchinchi
invoice'da** yonadi va approverni uni **e'tiborsiz qoldirishga** o'rgatadi —
modulning o'zi ogohlantirgan xato.

Shuning uchun `ROUND_TRAILING_ZEROS = 3` **nom bilan e'lon qilindi** va
`outlier_ratio` bilan **bir xil darajada** (operator konfiguratsiyasi, konservativ
default). Yashirin literal operator bahslasha olmaydigan chegara bo'lardi.

## 37. Tekshirilgan, nuqson **yo'q**

**`format_amount` / `normalize` — pul yadrosi:**
1 600 tasodifiy qiymat, 4 valyuta, `format → parse` **aylanma yo'li**:
**0 nomuvofiqlik**. `divmod` faktor bilan to'g'ri; funksiya o'z docstring'ida
"arifmetika uchun ishlatilmaydi" deb aytadi.

**`MINOR_UNITS` faqat bitta joyda e'lon qilingan** — ko'chirilgan nusxa yo'q
(bu muhim: nusxa bo'lsa, biri yangilanib ikkinchisi eskirardi).

**`weekend_date` — to'g'ri dizayn:**
`if weekday >= 5 and parsed_prior and weekday_history == 0` — ya'ni faqat
**shu yetkazib beruvchining o'z naqshidan** chetlashishda yonadi. Har doim
yakshanba kuni hisob-faktura chiqaradigan yetkazib beruvchi uchun **jimgina turadi**.
Sanani o'qib bo'lmasa (`parsed_prior == 0`) ham **jim** — "tekshirdim" deb
ko'rsatmaydi.

## 38. Natijalar

| O'lchov | Oldin | Keyin |
|---|---|---|
| `test_documents` | 90 | **93** |
| Probe xossalari | 48 | **62** |
| Registry | 88 | 88 (o'zgarmadi) |

**Qo'riqchi tekshirildi:** eski shart qaytarilganda → **1 failure, 2 errors**.

## 39. V faza saboqlari

1. **Birlik — bu spetsifikatsiya.** "Minor birlikda hisoblash" iborasi o'z-o'zidan
   to'g'rilikni kafolatlamaydi: savol **qaysi birlikda so'ralayotgani** muhim.
   `.py` fayl docstring'i o'lchov emas — 18-chi tasdiq.
2. **Ko'p valyutali kod bir valyutada sinaladi.** Barcha testlar `UZS` ishlatardi;
   USD yo'li **hech qachon yugurilmagan**. Testdagi valyuta — sinov qamrovi.
3. **Bitta son ikki savolga javob berolmaydi.** "Dumaloqmi?" va "qanchalik
   dumaloq?" — ajratildi.
4. **Chegara e'lon qilinishi kerak, yashirilmasligi.** `ROUND_TRAILING_ZEROS`
   nom bilan turadi, chunki to'g'ri qiymatni faqat operator biladi.

---

# VI FAZA — `documents.duplicates` hisobot shakli (2026-09-20)

## 40. Nima o'lchandi

V faza tugagach, **ayni shu modulda** uchinchi nuqson topildi. Bu safar
`duplicates()` ning **hisobot shakli**.

```python
same = (existing['total_minor'] == document['total_minor']
        and existing['currency'] == document['currency'])
...
'amount_changed': None if same else {
    'from': format_amount(existing['total_minor'], existing['currency']),
    'to': format_amount(document['total_minor'], document['currency']),
},
```

**Mantiq to'g'ri** — `altered` summa **va** valyutani birga solishtiradi, ya'ni
1000 UZS ≠ 1000 USD. **Lekin hisobot noto'g'ri.**

### 40.1 Ikki xil o'zgarish bitta juftlikka siqilgan

O'lchandi — bir xil raqam, valyuta o'zgargan:

| Kirish | `altered` | `amount_changed` |
|---|---|---|
| 1000 UZS → 1000 USD | `True` | `{'from': '1000', 'to': '1000.00'}` |

**Approver ko'rgan narsa:** "summa 1000 dan 1000.00 ga o'zgardi". Bu **summa
o'zgargani haqidagi xabar**, lekin **summa o'zgarmagan** — o'zgargan narsa
**valyuta**. Approver mavjud bo'lmagan narx farqini qidiradi.

`from` va `to` **ikki xil valyutada** formatlangan: `'1000'` (UZS) va `'1000.00'`
(USD). Ya'ni juftlikning ikki tomoni **solishtirib bo'lmaydigan** birlikda.

## 41. Tuzatish

Ikki xil savol → **ikki xil kalit**:

| Kalit | Ma'nosi |
|---|---|
| `amount_changed` | summa o'zgarganmi (bir xil valyutada) |
| `currency_changed` | valyuta o'zgarganmi |
| `amount_delta_minor` | farq, **faqat** valyutalar bir xil bo'lganda |
| `same_amount` / `same_currency` | ikki o'lchov alohida |

Yordamchi `_changed_amount()` ajratildi, chunki uch shoxobchani ichma-ich
shartli ifoda sifatida yozish o'qib bo'lmas edi — va bu faylda **aynan shunday**
"o'qib bo'lmaydigan shartli" avval nuqson bergan.

**Valyuta boshqacha bo'lganda `amount_delta_minor` — `None`.** Kurs bo'lmasa
farqni hisoblash **o'ylab topilgan son** bo'lardi. Modul kurs haqida hech narsa
bilmaydi, shuning uchun **jim turadi**.

O'lchandi — uch holat, endi ajratiladi:

| Holat | `amount_changed` | `currency_changed` | `amount_delta_minor` |
|---|---|---|---|
| 1000 UZS → 1000 UZS | `None` | `None` | `0` |
| 1000 UZS → 1500 UZS | `1000→1500` | `None` | `500` |
| 1000 UZS → 1000 USD | `1000→1000` + izoh | `UZS→USD` | **`None`** |

## 42. Natijalar

| O'lchov | Oldin | Keyin |
|---|---|---|
| `test_documents` | 93 | **96** |
| Probe xossalari | 62 | **76** |
| Offline to'plam | 2341 | **2345** |

**Signatura aynan:** `failures=1, errors=149, skipped=1`.

**Qo'riqchi tekshirildi:** eski shakl qaytarilganda → probe **1 FAIL**,
`test_documents` **1 failure**. Ikkalasi ham ushladi.

## 43. VI faza saboqlari

1. **Hisobot ham kod.** `altered` to'g'ri edi; noto'g'ri bo'lgan narsa —
   **hisobotning shakli**. Mantiq to'g'ri bo'lsa ham, o'qiladigan natija
   yolg'on gapirishi mumkin.
2. **Bir juftlikka ikki kattalikni siqma.** `from`/`to` — **bitta** kattalik
   uchun. Ikki kattalik o'zgarsa, **ikki** juftlik kerak.
3. **Solishtirib bo'lmaydigan narsani solishtirma.** Ikki xil valyutadagi farq —
   kurs talab qiladi. Kurs yo'q bo'lsa, javob `None`, taxmin emas.
4. **O'qib bo'lmaydigan shartli ifoda — nuqsonning uyidir.** Uch shoxobchani
   ifodaga siqish o'rniga nomlangan funksiya ajratildi.

---

# VII faza — satr arifmetikasi va o'lik akkumulyator

## 44. Nima o'lchandi

Skanerlash **o'lchov birligi** bo'yicha emas, **kod o'likmi yoki yo'qmi** va
**qo'riqchi nima qilishga va'da bergan** bo'yicha o'tkazildi.

Ikki mustaqil nuqson topildi.

### 44.1. `match()` ichidagi o'lik akkumulyator

`compared` nomli ro'yxat ikki shoxobchada to'ldirilar, lekin **hech qachon
o'qilmasdi**. AST orqali isbotlandi: `.append` — atributga murojaat, haqiqiy
o'qish emas. Qaytariladigan kalitlar bilan solishtirildi:

```
['checks', 'complete', 'currency', 'invoice_total', 'missing',
 'status', 'tolerance']
```

`compared` bu ro'yxatda yo'q. Ya'ni u **o'lik yuk**: hisoblanadi, xotira
egallaydi, o'quvchini chalg'itadi va hech qanday qarorga ta'sir qilmaydi.

### 44.2. Teskari qo'riqchi — `_line_items`

Bu **haqiqiy** nuqson va u jimgina yolg'on gapirardi.

Hujjat o'z arifmetikasiga zid bo'lsa, qo'riqchi uni **rad etishi** kerak. Kod
esa shunday edi:

```python
items.append({'description': description, 'quantity': quantity,
              'amount': amount})
...
summed = sum(item['amount'] for item in items)
```

`quantity` **tekshirilar** va **saqlanar**, lekin yig'indiga **qo'shilmasdi**.
Natijada:

| Hujjat | Nima bo'lishi kerak | Nima bo'lardi |
|---|---|---|
| `2 x 5000`, jami `10000` | Qabul | **RAD** — "o'ziga zid" |
| `2 x 5000`, jami `5000` | Rad | **QABUL** |

Ya'ni qo'riqchi **aynan o'zi ushlashi kerak bo'lgan ziddiyatni kafolatlar edi**.
To'g'ri hisob-faktura rad etilardi, noto'g'risi o'tib ketardi.

## 45. Tuzatish

`amount` — satrning **birlik narxi**. Yangi maydon qo'shildi:

```python
items.append({'description': description, 'quantity': quantity,
              'amount': amount, 'line_total': quantity * amount})
```

Va bitta **nomlangan** funksiya ajratildi:

```python
def line_items_total(items):
    """The sum of the LINE totals: ``quantity * amount`` per line, then added."""
    return sum(item['line_total'] for item in items)
```

`normalize()` endi `summed = line_items_total(items)` deb chaqiradi.
`match()` dan o'lik `compared` ro'yxati olib tashlandi.

## 46. Tekshirilgan, nuqson **yo'q**

- **1 600 tasodifiy `format_amount → normalize` aylanishi**, 4 valyuta,
  **0 nomuvofiqlik**.
- `MINOR_UNITS` **faqat bir joyda** e'lon qilingan.
- `weekend_date` ta'minotchining **o'z** naqshiga solishtiradi va sanalar
  o'qilmasa — jim qoladi.
- Tolerantlik chegarasi **ikki tomondan** (simmetrik, inklyuziv); `difference`
  har doim **absolyut** kattalik; `reason` ikki yo'nalishda ham to'g'ri o'qiladi.

## 47. Natijalar

| O'lchov | Oldin | Keyin |
|---|---|---|
| `test_documents` | 96 | **100** |
| Probe xossalari | 76 | **98** |
| Offline to'plam | 2345 | **2349** |

**Signatura aynan:** `failures=1, errors=149, skipped=1`.

**Qo'riqchi tekshirildi:** `line_items_total` eski shaklga qaytarilganda →
probe **2 FAIL**, `test_documents` **3 error**. Ikkalasi ham ushladi.

## 48. VII faza saboqlari

1. **To'ldirilgan akkumulyator ham o'lik bo'lishi mumkin.** Ro'yxat ikki
   shoxobchada to'ldirilib, hech qachon o'qilmasa — bu **o'lik yuk**, va u
   "o'lik chegara yo'q chegaradan yomon" qoidasining yana bir shakli.
2. **Qo'riqchi ham kod.** Uning **teskari** ishlashini faqat **teskari tomondan**
   o'lchash aniqlaydi. "Qo'riqchi bor" — "qo'riqchi to'g'ri ishlaydi" degani emas.
3. **Tekshirilgan, lekin ishlatilmagan maydon — eng jim nuqson.** `quantity`
   tekshirilardi, saqlanardi, va hech narsaga ta'sir qilmasdi. Hech qanday
   xatolik chiqmasdi.
4. **Satr o'z-o'zidan o'qilishi kerak.** `2 x 5000`, `quantity` va `amount`
   bilan yozilsa, kod ham **aynan shunday** hisoblashi shart — boshqa hech narsa
   emas.
5. **Sonni "kattaligi" bilan taqqoslash yetarli emas.** `amount` atamasi ikki
   xil ma'noni (birlik narxi / satr jami) ko'tarishi mumkin edi; nomni
   aniqlashtirish (`line_total`) muammoning yarmini yopadi.

---

# VIII faza — sukut bo'yicha qabul qilinadigan chegaralar va boshqa yuzalar

## 49. Nima o'lchandi

Skanerlash **yangi o'lchov** bo'yicha o'tkazildi: **"sukut bo'yicha qanday qiymat
qabul qilinadi"** — AST bilan butun runtime bo'ylab standart argument qiymatlari
yig'ildi. Shu bilan bir qatorda savol berildi: **"bu o'quvchi kim tomonidan
chaqiriladi"** — bu safar faqat **runtime** emas, **butun mahsulot**, shu
jumladan HTTP yuzasi.

Ikki nuqson sinfi topildi — ikkalasi ham **eski sinfning yangi joyda
takrorlanishi**.

### 49.1. `knowledge.search` kesilganini aytmasdi

Qaytariladigan shakl:

```
['collection', 'matches', 'retrieval', 'semantic_fact_check']
```

`limit` **5** bilan cheklangan. O'lchandi:

| Korpus | limit | qaytdi | haqiqatda kesildi | javob shakli |
|---|---|---|---|---|
| 3 | 4 | 3 | yo'q | o'sha |
| 4 | 4 | 4 | yo'q | o'sha |
| 5 | 4 | 4 | **ha** | o'sha |
| 50 | 5 | 5 | **ha** | o'sha |
| 5 | 5 | 5 | yo'q | o'sha |

Ya'ni **50 ta mos bo'lak va 5 ta mos bo'lak bir xil javob beradi**. Bu
**retrieval** natijasi: agent aynan shu matnlarga tayanib javob yozadi. 50 tadan
5 tasini "korpus shu haqda shuni aytadi" deb o'qish — **o'ndan birini butun deb**
o'qish.

Bu butun audit boshlangan nuqsonning **aynan o'zi**, faqat boshqa joyda.

**Tuzatish:** `matched`, `returned` va `truncated` qo'shildi. `truncated` —
`matched > returned`, ya'ni **populyatsiya bilan** taqqoslanadi, `limit` bilan
emas: aynan `limit` ta mos kelgan korpus **kesilgan deb hisoblanmaydi**.

### 49.2. To'rt o'quvchi HTTP orqali ochiq edi

`briefing.ledger`, `escalation.ledger`, `reengagement.ledger` va
`supervisor.history` — to'rttasi ham **yalang'och ro'yxat** qaytarardi va
to'rttasining docstringida shunday yozilgan edi:

> _"Nothing in the runtime calls this yet, so it is not a live defect; it is a
> trap."_

Bu xulosa **noto'g'ri savol** bergan edi: **runtime** kim chaqiradi deb
so'ralgan, lekin **mahsulot** kim yeta oladi deb so'rash kerak edi. To'rttasi ham
HTTP marshruti orqali ochiq:

```
GET /{tenant}/briefing/{schedule}/ledger      -> entries
GET /{tenant}/escalation/{schedule}/ledger    -> entries
GET /{tenant}/reengagement/{policy}/ledger    -> entries
GET /{tenant}/supervisor/history              -> routes
```

Ya'ni operator "nechta yugurish bo'ldi" deb so'rasa — **sahifa o'lchami** bilan
javob olardi. Bu `erp.posting_status` nuqsonining **aynan o'zi**.

**Tuzatish:** to'rttasiga `with_total=True` qo'shildi — sahifa va populyatsiya
**bir o'qishda** sanaladi, shuning uchun ikkisi bir-biriga zid bo'la olmaydi.
Standart shakl **o'zgarmadi** (yalang'och ro'yxat), shuning uchun mavjud
chaqiruvchilar buzilmadi.

## 50. Natijalar

| O'lchov | Oldin | Keyin |
|---|---|---|
| `test_knowledge` | 31 | **34** |
| `test_briefing` | 44 | **47** |
| `test_escalation` | 109 | **112** |
| `test_reengagement` | 29 | **32** |
| `test_supervisor` | 53 | **56** |
| Probe xossalari | 98 | **143** |

**Qo'riqchilar tekshirildi:**
- `knowledge` va `supervisor` qaytarilganda → probe **9 FAIL** (yorilib emas,
  **hisobot berib** — chunki bayroqning **yo'qligi** nuqsonning o'zi).
- To'rtta o'quvchi yalang'och ro'yxatga qaytarilganda → suite
  **1 failure, 9 error**.

## 51. VIII faza saboqlari

1. **"Runtime chaqirmaydi" — xavfsizlik dalili emas.** Yuzani **mahsulot**
   bo'yicha o'lchash kerak: HTTP marshruti ham chaqiruvchidir.
2. **Bir sinf bir joyda tugamaydi.** `truncated` nuqsoni yetti chaqiruv
   nuqtasida topilgan edi; sakkiz fazadan keyin yana **besh** joyda chiqdi.
   Sinfni **shakli** bo'yicha qidirish kerak, **moduli** bo'yicha emas.
3. **Retrieval natijasida kesish — eng qimmat kesish.** Qaytarilgan matn agent
   tayanadigan **dalil**. 5/50 ni 5/5 deb berish modelni jim ravishda
   noto'g'ri asosga o'tqazadi.
4. **Probe yorilib emas, hisobot berishi kerak.** Bayroqning **yo'qligi** — bu
   ham o'lchov natijasi. `KeyError` bilan to'xtagan probe qolgan 100 ta xossani
   ko'rsatmaydi.
5. **Standart shaklni saqlash — tuzatishning yarmi.** `with_total` **opt-in**
   bo'lgani uchun 247 ta mavjud test **o'zgarmasdan** o'tdi.

---

# IX faza — bayroqning hosilasi va uni o'lchaydigan test

## 52. Nima o'lchandi

Bu fazada **o'lchov birligi** yangi edi: **"bayroq nima bilan taqqoslanadi"**
va **"uni o'lchaydigan test qayerda turadi"**.

Butun runtime bo'ylab `truncated` / `cut` / `more` bayroqlari AST bilan
yig'ildi — **30 ta** — va har biri **hosilasi** bo'yicha tasniflandi:

- **populyatsiya bilan** (`len(ordered) > limit` — bunda `ordered` **to'liq**
  to'plam, qaytariladigani esa `ordered[:limit]`) → **to'g'ri**;
- **faqat chegara bilan** (`len(natija) >= limit`) → **nuqson**.

Natija: **30 tadan 30 tasi to'g'ri.** Bu audit boshlangan nuqson sinfi
**butun kod bo'ylab yopilgan**.

To'rttasi dastlab "shubhali" ko'rindi (`assets.children`, `sheets._read`,
`vision.summary`, `workforce.attendance`) — chunki o'zgaruvchi nomlari
evristikaga tushmadi. Har biri **o'qib emas, o'lchab** tekshirildi: to'rttasida
ham bayroq **to'liq to'plamni** (`ordered`, `values`, `absent`) chegara bilan
taqqoslaydi, qaytariladigani esa **kesim** (`ordered[:limit]`, `absent[:limit]`).

## 53. Haqiqiy topilma — test bir tomonni o'lchardi

Bayroqlarning **o'zi** to'g'ri bo'lsa ham, ularni o'lchaydigan **testlar**
ko'p joyda faqat **kesilgan tomonni** tekshirardi. Kesilgan tomon
`len(natija) >= limit` bilan ham, to'g'ri predikat bilan ham **bir xil** javob
beradi — shuning uchun u hech narsani isbotlamaydi.

O'lchandi:

| Modul | Kesilgan tomon | Aynan mos tomon | Xulosa |
|---|---|---|---|
| `assets` | 2 | 3 | ikki tomon |
| `business_graph` | 0 | 1 | faqat aynan mos |
| `erp` | 1 | 1 | ikki tomon |
| `escalation` | 1 | 1 | ikki tomon |
| `inventory` | 1 | 1 | ikki tomon |
| `knowledge` | 1 | 2 | ikki tomon |
| `manufacturing` | 3 | 1 | ikki tomon |
| `oee` | 1 | 1 | ikki tomon |
| `oversight` | 2 | 2 | ikki tomon |
| `sheets` | 1 | 1 | ikki tomon |
| **`telephony`** | 2 | **0** | **faqat kesilgan tomon** |
| **`vision`** | 1 | **0** | **faqat kesilgan tomon** |
| `workforce` | 3 | 3 | ikki tomon |

`assets.children` da esa **umuman test yo'q** edi.

## 54. Tuzatish

Uchta modulga aynan mos tomon qo'shildi:

- `test_assets`: `children` uchun **ikkita** yangi test (kesilgan **va** aynan
  mos); `descendants` uchun aynan mos tomoni.
- `test_telephony`: `call_events` uchun aynan mos tomoni.
- `test_vision`: `station_event` uchun aynan mos tomoni.

## 55. O'z xatom — chegara **ustida** turmagan test

Birinchi urinishda yangi testlar `limit=99` va `limit=MAX_EVENTS` bilan
yozildi. Ular **yashil** o'tdi — lekin teskari kodda ham **yashil** o'tdi.

Sabab: `matched >= limit` da `5 >= 99` — **False**. Ya'ni chegara
populyatsiyadan **uzoq** turgan ekan, **ikki predikat ham bir xil** javob
beradi. Test **aynan mos** holatni umuman o'lchamagan — u **chegaraviy testga
o'xshardi, lekin emas edi**.

Bu **testning o'zida** bo'lgan nuqson: **ko'rinishi to'g'ri, o'lchovi yo'q**.
Tuzatildi: chegara **aynan** populyatsiyaga qo'yildi (`limit=3` uchta hodisa
uchun, `limit=5` beshta uchun). Shundan keyin to'rttasi ham teskari kodda
**yiqildi**.

## 56. Natijalar

| O'lchov | Oldin | Keyin |
|---|---|---|
| `test_assets` | 57 | **59** |
| `test_telephony` | 138 | **139** |
| `test_vision` | 48 | **49** |

**Qo'riqchilar tekshirildi:** bayroqlar `> limit` dan `>= limit` ga
qaytarilganda → **4 failure** (assets, telephony ×2, vision). `knowledge`
predikati qaytarilganda → **1 failure**. Hammasi qaytarildi, hammasi yashil.

## 57. IX faza saboqlari

1. **Chegaraviy test chegarada turishi shart.** Populyatsiyadan uzoq turgan
   chegara ikki predikatni **bir xil** qiladi va hech narsani o'lchamaydi.
   "Yashil o'tdi" — "o'lchadi" degani emas.
2. **Testni teskari kodda sinab ko'r.** Qo'riqchini **ishlamaydigan** kodda
   ham yashil o'tadigan test — qo'riqchi emas, **bezak**.
3. **Bayroq to'g'ri bo'lishi yetarli emas; uni o'lchaydigan test ham to'g'ri
   bo'lishi kerak.** Bayroq 30/30 to'g'ri edi, lekin ikki modulda uni
   **hech narsa** o'lchamasdi.
4. **Nom evristikasi yolg'onchi.** To'rtta "shubhali" bayroqning hammasi
   to'g'ri edi; ularni faqat **o'qib emas, o'lchab** aniqlash mumkin.
5. **Bir sinf yopilganini e'lon qilish uchun uni butun kod bo'ylab sanash
   kerak.** 30/30 — bu "ko'rinadi to'g'ri" emas, **sanalgan** natija.

---

## 58. X faza: istisno turlari va mapper chetlab o'tilishi

Yangi o'lchov o'qi: **qaysi istisno turi bilan rad etamiz**. Sabab oddiy —
API qatlamining `call()` mapperi oltita turni status kodiga aylantiradi:

| Tur | Kod |
|---|---|
| `RateLimited` | 429 |
| `Forbidden`, `AuthenticationError` | 403 |
| `NotFound` | 404 |
| `Conflict` | 409 |
| `ValueError`, `LookupError` | 422 |

Ro'yxatda `TypeError` **yo'q**. Ya'ni o'qish to'g'riligi uchun yozilgan
**qo'riqchi** `TypeError` ko'tarsa, chaqiruvchining oddiy xatosi (noto'g'ri
argument turi) operatorga **500** bo'lib boradi: ayb chaqiruvchida emas,
serverda deb ko'rsatiladi. Bu — audit boshidan beri uchragan naqshning yana
bir ko'rinishi: **hisob-kitob to'g'ri, lekin xato yo'li noto'g'ri**.

## 59. O'lchov 1: `raise` joylari tur bo'ylab

Butun runtime bo'ylab **867** ta `raise` joyi sanaldi va tur bo'yicha
guruhlandi — **26 xil tur**:

| Tur | Joylar |
|---|---|
| `ValueError` | **477** |
| `Forbidden` | 148 |
| `Conflict` | 54 |
| qolgan 23 tur | 188 |

`ValueError` hukmron. Bu **konventsiya**: runtime o'zining chegaraviy
o'quvchilarida chaqiruvchining xatosini `ValueError` bilan rad etadi. Demak
har qanday `TypeError` shu konventsiyadan **chetga chiqish**, tasodif emas.

## 60. O'lchov 2: mapperni chetlab o'tuvchi marshrutlar

53 marshrut AST bo'ylab tekshirildi: qaysilari `call(...)` ishlatmaydi va
mahalliy `try` ham yo'q. **15** shunday marshrut topildi:

`get_identity`, `get_catalog`, `tasks`, `audit`, `inbox`, `devices`,
`reengagement_list`, `reengagement_ledger`, `reengagement_sync`,
`briefing_list`, `briefing_ledger`, `escalation_list`, `escalation_ledger`,
`supervisor_sections`, `supervisor_history`.

Bu **o'z-o'zidan xato emas** — ba'zi marshrutlar ataylab mapperni chetlab
o'tadi. Savol shu: ular **istisno ko'taradimi**?

## 61. O'lchov 3: o'ylash emas, haydash

Har bir chetlab o'tuvchi marshrutning runtime nishoni **haydaldi**. Natija:

| Manba | Buzuq kirish bilan nima qiladi |
|---|---|
| `briefing/escalation/reengagement/supervisor.ledger` | `min(max(1,int(limit)),500)` — **qisqartiradi** |
| `engine.list_tasks` | `min(200,max(1,limit))` — **qisqartiradi** |
| `sync_ledger` | har qanday policy id ni **qabul qiladi** |

Ya'ni **bugungi kunda haqiqiy 500 yo'q**. Mapper chetlab o'tilgan, lekin
chetlab o'tilgan yo'lda **hech narsa ko'tarilmaydi**. Bu muhim: "mapper
chetlab o'tilgan" degan topilma **o'z-o'zidan zaiflik emas**, u faqat
**imkoniyat**. Zaiflik bo'lishi uchun ko'tariladigan istisno ham kerak.

## 62. Bitta istisno: `engine.list_tasks`

O'lchov bitta chetga chiqishni topdi:

```
engine.list_tasks('t', 'x')
  -> TypeError: '>' not supported between instances of 'str' and 'int'
```

`min(200, max(1, limit))` — `max(1, 'x')` ni hisoblashga urinadi va
`TypeError` beradi. Tur mappperda yo'q → **500**.

## 63. U bugun yetib boriladimi? — o'lchanadi, o'ylanmaydi

"Butun repo bo'ylab AST" skani: `list_tasks` **faqat** tenant bilan
chaqiriladi. `/tasks` marshruti `limit` parametrini **umuman olmaydi**.

Demak `TypeError` bugun **yetib borilmaydi** — bu **yashirin tuzoq**, tirik
nuqson emas. Lekin audit shu shaklni qayta-qayta topgani uchun u **baribir**
tuzatildi: bir kuni `/tasks` ga `limit` qo'shiladi va o'sha kuni 422 emas,
500 bo'ladi.

## 64. Tuzatish

`list_tasks` endi runtime konventsiyasiga bo'ysunadi: **butun son bo'lmagan
limit — `ValueError`**, oraliqdan tashqari butun son esa **rad etilmaydi**,
**qisqartiriladi**:

| Kirish | Xatti-harakat |
|---|---|
| `limit='x'`, `None`, `True`, `1.5`, `[2]`, `{}` | `ValueError: limit must be an integer` |
| `limit=500` | 200 ga qisqartiriladi (shift), sahifa qaytadi |
| `limit=0`, `-5` | 1 ga ko'tariladi (pol), sahifa qaytadi |

Ikki chekka — **ikki xil qaror**: yuqorisi **shift** (sahifada 200 tadan
ko'p bo'lmaydi), pastkisi **pol** (nol qatorli sahifa hech qachon kutilgan
narsa emas). Shuning uchun ikkalasi **alohida** tekshiriladi.

`True` alohida qayd etishga arziydi: `bool` — `int` ning vorisi, shuning uchun
`min(200,max(1,True))` **xato bermaydi**, u shunchaki 1 qatorli sahifa
qaytaradi. Qo'riqchi `isinstance(limit,bool)` ni **nomma-nom** rad etadi —
chunki jimgina ishlaydigan noto'g'ri sahifa ko'rinadigan xatodan yomonroq.

## 65. Natijalar

| Ko'rsatkich | Oldin | Keyin |
|---|---|---|
| `test_engine` | 55 | **57** |
| Probe xossalari | 143 | **159** |
| Suite soni | 2376 | **2378** |

**Qo'riqchi tekshirildi (revert):** qo'riqchi `if False:` ga aylantirilganda
→ `test_engine` **1 error** (`TypeError` aynan chiqadi) va probe **5 FAIL**
(qolgan 148 o'tadi) — probe **yiqilmaydi**, u **hisobot beradi**. Tiklangandan
keyin: test yashil, probe **159/159**.

## 66. X faza saboqlari

1. **Mapper chetlab o'tilgani — zaiflik emas, imkoniyat.** Zaiflik bo'lishi
   uchun **ko'tariladigan istisno** ham kerak. Ikkalasini alohida o'lcha.
2. **"Mavjud emas" — tuzatmaslik uchun sabab emas, agar shakl noto'g'ri
   bo'lsa.** Yetib borilmaydigan tuzoq — hali ham tuzoq; u qo'shni qator
   qo'shilganda tirik bo'ladi.
3. **Konventsiya — o'lchov birligi.** 477 `ValueError` va 54 `Conflict` shuni
   ko'rsatadiki, bitta `TypeError` — tasodif emas, **chetga chiqish**.
4. **`bool` — `int` ning vorisi.** `True` jimgina 1 ga aylanadi. Turni
   tekshirganda bool ni **nomma-nom** ayirish kerak, aks holda jimgina
   noto'g'ri natija olasan.
5. **Sahifa chegarasining ikki uchi — ikki xil qaror.** Shift va pol bir xil
   ko'rinadi, lekin biri "ko'p so'radingiz", ikkinchisi "nol so'radingiz".
   Ikkalasini alohida sinash kerak.

---

## 67. XI faza: test tomonidagi ko'zgu bo'shlig'i

IX fazada test qamrovi **modul bo'ylab** o'lchandi va jadval chiqdi. O'sha
jadvalda bitta qator **"faqat aynan mos"** deb belgilangan edi:

| Modul | Kesilgan tomon | Aynan mos tomon |
|---|---|---|
| `business_graph` | **0** | 1 |

IX fazada `telephony` va `vision` (teskari holat) tuzatildi, lekin
`business_graph` **qoldirilgan** edi. XI faza aynan shu qatorni tekshiradi:
teskari holat ham xuddi shunday ko'r.

## 68. O'lchov: har bir test qaysi tomonda turadi

`business_graph` ning chegaraviy testlari **haydaldi** va har biri qaysi
tomonda turishi o'lchandi:

| Test | Fixture populyatsiyasi | Limitlar | Qaysi tomon |
|---|---|---|---|
| `test_search_truncation_is_true_only_when_a_match_was_left_out` | `stock==0` → **1** | 1, 2, 3 | aynan mos / ortiqcha — **kesilgan tomon yo'q** |
| `test_conflicts_truncation_is_false_when_the_conflict_list_fits` | konflikt → **1** | 1 | aynan mos — **kesilgan tomon yo'q** |
| `test_timeline_truncation_measures_the_boundary_from_both_sides` | hodisa → **2** | 1, 2, 3 | **ikki tomon** |

Ya'ni `search` va `conflicts` **faqat** aynan mos tomonda o'lchangan.
Testning **nomi** "only when a match was left out" deb va'da beradi, lekin
kod hech qachon matchni **tashlab ketmaydi**. Noto'g'ri predikat ikkala
tomonda ham bir xil javob beradi — shuning uchun bu testlar `>` va `>=` ni
**ajratmaydi**.

## 69. Nima uchun fixture almashtirish yetarli emas

Birinchi urinishda yangi fixture **Sheets transportidan** qurildi. Bu
**ishlamadi** va sabab muhim: `search` o'z identifikatorlarini **ulangan
manbadan** (ERP sqlite jadvalidan) oladi, skriptlangan transportdan emas.
Shuning uchun transportni o'zgartirish **populyatsiyani o'zgartirmaydi**.

To'g'ri yo'l — **haqiqiy jadvalni seed qilish**:

```python
def _seed_stock_zero_products(self, count):
    db = sqlite3.connect(self.erp)
    try:
        db.execute("DELETE FROM products WHERE sku LIKE 'CUT-%'")
        db.executemany('INSERT INTO products VALUES(?,?,?)',
                       [(f'CUT-{i}', 1000 + i, 0) for i in range(count)])
        db.commit()
    finally:
        db.close()
```

## 70. O'z xatom — yana hisob, yana men

Yangi `search` testi `population=2` deb **qabul qildi**. Kod `limit=2` da
`truncated=True` dedi va test **yiqildi**.

O'lchandi:

```
population      : 3 ['SKU-77', 'CUT-0', 'CUT-1']
truncated@100   : False
limit=1: rows=1 truncated=True
limit=2: rows=2 truncated=True
limit=3: rows=3 truncated=False
```

Populyatsiya **3**, chunki `setUp` ning o'z fixture'i allaqachon bitta
`stock==0` mahsulot beradi. Kod **to'g'ri**; mening **kutilmam** noto'g'ri
edi. Bu — IX fazadagi xatoning **aynan takrori**: fixture arifmetikasini
o'ylab topdim, o'lchamadim.

Tuzatish: test endi populyatsiyani **o'lchaydi**, qabul qilmaydi:

```python
full = self.call('graph.search', {..., 'limit': 100}, ...)
population = len(full['matches'])
self.assertEqual(3, population, f'fixture yields {population} stock-0 products')
self.assertFalse(full['truncated'], 'a limit of 100 cannot cut a population of 3')
```

## 71. Tuzatish va tekshiruv

Ikki yangi test qo'shildi:

- `test_search_reports_a_cut_when_a_match_is_actually_left_out` — **kesilgan
  tomon**: 2 ta `stock==0` mahsulot seed qilinadi, populyatsiya o'lchanadi (3),
  `limit=1` kesilgan, `limit=3` va `4` kesilmagan bo'lishi shart.
- `test_conflicts_reports_a_cut_when_a_conflict_is_actually_left_out` — 3 ta
  ziddiyatli mahsulot seed qilinadi, `limit=1,2,3,4` to'rt tomonni o'lchaydi.

**Qo'riqchi tekshirildi (revert):** uchta predikat (`_collect_identifiers`,
`search`, `conflicts`) `>=` dan `>` ga qaytarilganda → `test_business_graph`
**6 failure** (2 yangi + 4 mavjud). Tiklangach: **174 test OK**.

**O'z revert xatom:** birinchi urinishda faqat **ikki** predikat qaytarildi —
uchinchisi (`search`, 8 bo'shliqli indent) o'tkazib yuborildi, chunki
`replace()` satri 12 bo'shliq bilan yozilgan edi. Shu sababli faqat
`conflicts` testi yiqildi va men "search testi ajratmaydi" deb **noto'g'ri**
xulosa chiqarishimga sal bo'ldi. `grep` bilan predikatlarni **sanalgan**
holda tekshirish shart.

## 72. Natijalar

| Ko'rsatkich | Oldin | Keyin |
|---|---|---|
| `test_business_graph` | 172 | **174** |
| Probe xossalari | 159 | **159** (o'zgarmadi — kod to'g'ri edi) |
| Offline to'plam | 2380 | **2384** |

**Muhim ajratish:** bu fazada **kodda nuqson topilmadi**. Probe allaqachon
`search` va `conflicts` ni o'z harness'i orqali **ikki tomondan** o'lchardi va
**159/159** yashil edi. Yetishmayotgan narsa — **repo ichidagi suite**da
kesilgan tomonni o'lchaydigan test. Ya'ni bu faza **kod** haqida emas,
**dalil** haqida: "probe o'lchadi" ≠ "suite o'lchaydi".

## 73. XI faza saboqlari

1. **Ko'zgu bo'shlig'i ham bo'shliq.** Faqat kesilgan tomonni o'lchash ham,
   faqat aynan mos tomonni o'lchash ham — bir xil ko'r. Ikkalasi kerak.
2. **Probe o'lchashi suite o'lchashini bildirmaydi.** Kod to'g'ri bo'lishi va
   probe yashil bo'lishi repo testlari uni **qo'riqlaydi** degani emas.
3. **Populyatsiyani o'lcha, qabul qilma.** Fixture arifmetikasi — eng ko'p
   takrorlanadigan xatoyim. `limit=100` bilan to'liq ro'yxatni olib, undan
   populyatsiyani sanash — yagona ishonchli usul.
4. **Transport fixture'i har doim ham populyatsiyani o'zgartirmaydi.**
   Identifikatorlar **ulangan manbadan** kelsa, faqat jadvalni seed qilish
   ta'sir qiladi.
5. **Revertni `grep` bilan tasdiqla.** Bitta joyni qaytarib, boshqasini
   o'tkazib yuborish — "qo'riqchi ishlamayapti" degan **yolg'on** xulosaga
   olib keladi.

---

## 74. XII faza: vaqt va soat izchilligi

Yangi o'lchov o'qi: **vaqt**. Runtime `Engine(..., clock=...)` orqali
**in'ektsiya qilinadigan soat** oladi va u **bezak emas**: deadline, lease,
approval muddati, escalation oynasi — hammasi shu soatni o'qiydi. Savol:
**vaqtga bog'liq har bir javob shu soatni o'qiydimi?**

## 75. O'lchov: nechta joy devor soatini o'qiydi

Butun runtime bo'ylab `time.time()` / `datetime.now()` / `utcnow()` qidirildi:

| Modul | `engine.clock()` | Xom devor soati |
|---|---|---|
| `engine.py` | ko'p | 0 (manba) |
| `escalation` | 1 | 0 |
| `usage_budget` | 4 | 0 |
| `workforce` | 1 | 0 |
| **`whatsapp`** | **2** | **1** |

Faqat **bitta** xom o'qish qolgan edi. U `_now()` ning **default** i edi,
ya'ni qonuniy — lekin savol shu: **production yo'llari uni ishlatadimi?**

## 76. Real nuqson: send gate devor soatini o'qigan

`window_state(last_inbound, now=None)` — sof funksiya, `now` berilmasa
`time.time()` ga tushadi. Bu **to'g'ri dizayn**: enginesiz chaqiruvchi sof
savol beradi. Lekin ikki production yo'li bor edi:

| Yo'l | Chaqiruv | Soat |
|---|---|---|
| `whatsapp_window_reports` | `window_state(last, now)` | ✅ engine |
| **`whatsapp_text` (send gate)** | **`window_state(last)`** | ❌ **devor soati** |

Ya'ni **yuborish darvozasi** — modulning o'zi "131047 ning oldini olish uchun"
deb qurilgan joy — "mijoz 24 soatlik oynada-mi" savoliga platforma
**nazorat qilmaydigan** soat bilan javob berardi.

## 77. Nima uchun bu nazariy emas

`Engine(..., clock=...)` — **ommaviy** interfeys. Uchta holatda soat devor
soatidan farq qiladi:

1. **Test** — in'ektsiya qilingan soat bilan javob **qayta olinmaydi**, ya'ni
   test uni **qadab qo'ya olmaydi**.
2. **Replay** — o'tmishdagi hodisani qayta o'ynaganda oyna boshqa javob beradi.
3. **Hostlar orasidagi siljish** — platforma bir soat bilan ishlaydi, oyna
   boshqasi bilan.

Va aynan shu modul Meta ning `131047` xatosini oldini olish uchun yozilgan:
"oyna yopiq" bo'lsa, xabar **hech qachon yetib bormaydi**.

## 78. O'lchov: soatni surib ko'rish

Darvozani **decisive** o'lchash: faqat **engine soatini** surish, devor
soatiga tegmaslik.

```
--- engine clock = now ---
  yuborildi, provider chaqiruvi: 1
--- engine clock = now + 10 oyna ---
  STILL SENT, provider chaqiruvi: 1
  -> darvoza engine soatini e'tiborsiz qoldirdi
```

Tuzatishdan keyin:

```
--- engine clock = now + 10 oyna ---
  refused: Forbidden ... the 24-hour service window is closed
  -> darvoza endi engine soatini o'qiydi
```

## 79. Tuzatish

Yangi yordamchi — engine soatini o'qiydi, enginesiz stub uchun devor soatiga
qaytadi:

```python
def _engine_now(engine):
    clock = getattr(engine, 'clock', None)
    return clock() if callable(clock) else _now()
```

Ikki production chaqiruv nuqtasi ham engine soatini uzatadi:

```python
opened, _closes_at = window_state(last, _engine_now(engine))
```

`window_state` ning o'zi **o'zgarmadi** — uning `now=None` default'i to'g'ri
edi; nuqson **chaqiruv nuqtasida** edi.

## 80. Eng qimmatli topilma: testlar nuqsonni **qadab qo'ygan** edi

Tuzatishdan keyin **ikki test yiqildi**. Sabab o'lchandi:

```
RECENT (import vaqtida)  : 1789912845.23
time.time() hozir        : 1789912905.23
import -> hozir siljish  : 60.0 s
```

Testlar hodisani `time.time() - (WINDOW + 60)` bilan joylashtirardi — devor
soatiga nisbatan oynadan **60 s** narida (yopiq). Engine soati esa `RECENT`,
ya'ni **import vaqtidagi** `time.time() - 60`. Ikki soat **aynan 60 s** farq
qiladi va testning margini ham **aynan 60 s**.

Ya'ni bu testlar **faqat** darvoza engine soatini e'tiborsiz qoldirgani uchun
o'tardi. Ular nuqsonni **qadab qo'ygan** edi: kodni tuzatish ularni sindirdi.

Tuzatish: fixture'lar endi `self.clock.now` ga bog'lanadi — javob va savol
**bir xil soatni** bo'lishadi:

```python
self.seed_inbound('ali', wrote_at=self.clock.now - (WINDOW_SECONDS + 60))
```

## 81. Tuzatish va tekshiruv

Uchta o'zgarish:

1. `whatsapp._engine_now(engine)` qo'shildi.
2. Send gate endi `window_state(last, _engine_now(engine))` chaqiradi.
3. Ikki test fixture'i `time.time()` dan `self.clock.now` ga o'tkazildi, va
   yangi test qo'shildi: `test_the_send_gate_answers_using_the_engine_clock`.

**Qo'riqchi tekshirildi (revert):** chaqiruv nuqtasi `window_state(last)` ga
qaytarilganda → yangi test **1 failure** (`Forbidden not raised`), whatsapp
to'plamlari **1 failure** (aynan o'sha test, boshqa hech narsa), probe
**2 FAIL** (173 dan 171).

## 82. Probe'ning o'z xatosi

Probe'ning birinchi versiyasi `_engine_now(engine)` ni **to'g'ridan-to'g'ri**
chaqirardi va yashil o'tdi — **teskari kodda ham**. Sabab: u **yordamchini**
o'lchardi, **chaqiruv nuqtasini** emas. Nuqson aynan chaqiruv nuqtasida edi.

Tuzatish: probe endi `send` funksiyasining **manba matnini** tekshiradi —
`window_state(last, _engine_now(engine))` bor-yo'qligini va yalang'och
`window_state(last)` yo'qligini. Shundan keyin teskari kodda **2 FAIL**.

Bu IX fazadagi saboqning yana bir ko'rinishi: **yordamchi to'g'ri bo'lishi
chaqiruv nuqtasi to'g'ri degani emas.** O'lchov **nosozlik yo'lini** bosishi
shart.

## 83. Natijalar

| Ko'rsatkich | Oldin | Keyin |
|---|---|---|
| `test_whatsapp` | 119 | **120** |
| Probe xossalari | 159 | **173** |
| Offline to'plam | 2384 | **2385** |

**Sinf bo'ylab:** endi **to'rtta** vaqtga bog'liq modul (`escalation`,
`usage_budget`, `workforce`, `whatsapp`) ham engine soatini o'qiydi, va
**hech birida** e'lon qilinmagan xom devor soati qolmadi.

## 84. XII faza saboqlari

1. **In'ektsiya qilinadigan soat — shartnoma.** Agar `clock` parametri bor
   bo'lsa, **har bir** vaqtga bog'liq javob uni o'qishi shart; aks holda u
   bezak va javob qayta olinmaydi.
2. **Test nuqsonni qadab qo'yishi mumkin.** Fixture devor soatiga bog'langan
   bo'lsa, u kodning xatosini "to'g'ri" deb mustahkamlaydi. Tuzatish testni
   sindirsa — bu test **xatoni himoya qilgan**, degani.
3. **Sof funksiyaning default'i qonuniy, chaqiruv nuqtasi javobgar.**
   `window_state` to'g'ri edi; darvoza noto'g'ri edi.
4. **Yordamchini o'lchash — chaqiruv nuqtasini o'lchash emas.** Probe
   `_engine_now` ni chaqirib yashil bo'ldi, teskari kodda ham.
5. **Ikki soat orasidagi farq testning marginiga teng bo'lsa — test hech
   narsani o'lchamaydi.** 60 s siljish va 60 s margin: tasodif emas, yashirin
   bog'liqlik.

---

## 85. XIII faza: muddat chegarasi va testning turgan joyi

XI faza saboqni berdi: **chegaraviy test chegarada turishi shart**. XIII faza
o'sha qoidani **boshqa qatlamga** qo'llaydi — `app/` (identity, auth, session).

Savol oddiy: **muddat (expiry) qo'riqchilari qayerda o'lchangan?**

## 86. O'lchov: qaysi modulda xom soat ko'p

`app/` bo'ylab `time.time()` joylari sanaldi:

| Modul | Xom soat | Izoh |
|---|---|---|
| **`identity_store`** | **15** | session, TTL, throttle, invitation |
| `storage`, `customer360` | 4 | |
| qolgan 10 modul | 1–3 | |

`identity_store` — auth yadrosi, ya'ni soat xatosi bu yerda **xavfsizlik**
masalasi, to'g'rilik emas.

## 87. Muddat predikatlari — izchillik o'lchandi

`identity_store` da muddat o'qiydigan **to'rt** joy bor:

| Joy | Predikat | Ma'nosi |
|---|---|---|
| `_invitation` (196) | `expires <= now` | taklif muddati o'tgan |
| `validate_session` (273) | `expires <= now` | sessiya yaroqsiz |
| `rotate_session` (284) | `expires <= now` | refresh yaroqsiz |
| `list_sessions` (320) | `expires > now` | faqat tirik sessiyalar |

Uchtasi **inklyuziv** rad etadi, bittasi **eksklyuziv** qabul qiladi — ya'ni
ikkalasi **aynan to'ldiruvchi**. O'lchandi:

```
now < expires    refuse=False valid=True   agree=True
now == expires   refuse=True  valid=False  agree=True
now > expires    refuse=True  valid=False  agree=True
```

Ular **hamma lahzada** mos keladi, va tenglikda sessiya **o'lgan** deb
hisoblanadi — xavfsizlik chegarasi uchun **to'g'ri yo'nalish**.

**Ya'ni kodda nuqson yo'q edi.** Savol — **uni qo'riqlaydigan test** qayerda
turibdi?

## 88. Haqiqiy topilma — test chegaradan 57 yil narida

Mavjud test:

```python
def test_invitation_expired_rejected(self):
    ...
    c.execute('UPDATE p_invitations SET expires=1')
    with self.assertRaises(s.AuthenticationError): ...
```

`expires=1` — epoch 1 sekund, ya'ni **57 yil** o'tmishda. O'lchandi:

| Predikat | `expires=1` da | Test kutgan |
|---|---|---|
| `expires <= now` | rad etadi | rad etish ✅ |
| `expires < now` | rad etadi | rad etish ✅ |

**Ikkalasi ham o'tadi.** Ya'ni test **qaysi predikat kuchda ekanini
ajratmaydi** — u "rad etish sodir bo'ldi" ni isbotlaydi, **chegara qayerda**
ekanini emas. Bu IX fazadagi `limit=99` xatosining **aynan takrori**, faqat
boshqa qatlamda.

Ajratadigan yagona holat — **tenglik**:

```
expires=now, now=now-1 : <= False | < False  (bir xil)
expires=now, now=now   : <= True  | < False  (FARQ)  <- yagona ajratuvchi
expires=now, now=now+1 : <= True  | < True   (bir xil)
```

## 89. Tuzatish — to'rt chegara testi

Muzlatilgan soat bilan **aynan tenglikda** o'lchaydigan to'rt test qo'shildi:

- `test_invitation_expiring_exactly_now_is_rejected` — `expires == now` rad
  etilishi, va **bir sekund oldin** qabul qilinishi shart.
- `test_session_expiring_exactly_now_is_refused` — `validate_session` da
  xuddi shu chegara.
- `test_session_refresh_expiring_exactly_now_is_denied` — `rotate_session` da
  xuddi shu chegara (ikki **alohida** sessiya bilan).
- `test_list_sessions_hides_an_expiring_exactly_now_session` — ro'yxat ham
  rad etishning **to'ldiruvchisi** ekanini o'lchaydi.

Har biri **ikki tomonni** tekshiradi: tenglikda rad, bir sekund oldin qabul —
aks holda test "hammasini rad et" bilan ham o'tib ketardi.

**Qo'riqchi tekshirildi (revert):** to'rtta predikat (`<=` → `<`,
`>` → `>=`) qaytarilganda → `test_identity_hardening` **4 failure** (aynan
to'rtta yangi test). Eski `expires=1` testi **yashil qoldi** — u hech narsani
o'lchamasligini shu bilan isbotladi.

## 90. O'z xatolarim

1. **Noto'g'ri funksiya.** `s.authenticate(raw)` deb chaqirdim — u
   **login** funksiyasi, parol talab qiladi. To'g'risi `validate_session`.
2. **Bitta tokenni ikki marta ishlatdim.** `rotate_session` muddat o'tganda
   **butun oilani bekor qiladi** (rollback tiriltirmasligi uchun), shuning
   uchun retry o'lchovni buzdi. Ikki **alohida** sessiya bilan tuzatildi.
3. **Probe jadvalim xato edi.** `expires = now - 1` ("bir sekund keyin")
   holatida ham `<=`, ham `<` rad etadi — ya'ni ular **farq qilmaydi**. Men
   "farq qiladi" deb yozgandim. O'lchov tuzatdi: ajratuvchi **faqat tenglik**.
4. **Probe satr mosligi mo'rt edi.** Qo'riqchini bitta aniq qatorga moslab
   qidirdim, u topilmadi va probe **"qo'riqchi yo'q"** deb **yolg'on**
   xabar berdi. Endi **sanaydi**, bitta literalga bog'lanmaydi.

## 91. Natijalar

| Ko'rsatkich | Oldin | Keyin |
|---|---|---|
| `test_identity_hardening` | 27 | **31** |
| Probe xossalari | 173 | **181** |
| Offline to'plam | 2385 | **2389** |

## 92. XIII faza saboqlari

1. **Chegara qoidasi qatlamga bog'liq emas.** IX fazada runtime'da o'rganilgan
   saboq `app/` da ham **aynan** takrorlandi.
2. **"Rad etildi" ≠ "chegara to'g'ri".** Uzoq o'tmishdagi fixture rad etishni
   isbotlaydi, chegaraning **joyini** emas.
3. **Xavfsizlik chegarasida tenglik — o'lgan.** Sessiya muddati tugagan
   sekundda **yaroqsiz**; bu yo'nalish ataylab tanlangan.
4. **Ikki o'quvchi bir faktni o'qisa — ikkalasi o'lchanishi kerak.**
   `validate_session` va `rotate_session` bir sekundda ajralib qolmasligi
   uchun ikkalasi ham sinovdan o'tdi.
5. **Probe o'z xatosini ham ko'rsatadi.** Ikki "FAIL" kodda emas, mening
   jadvalim va satr mosligimda edi — va bu ham **o'lchov** natijasi.

## 93. XIV faza — tasdiq muddati (approval deadline): bitta chegara, ikki o'quvchi

Navbatdagi qadam runtime qatlamidagi **muddat chegaralari** edi. `platform_runtime`
bo'ylab `expires` / `deadline` / `lease` / `expiry` o'quvchilari supurildi
(`agent_loop.py`, `engine.py`, `connectors.py`, `google_adapters.py`,
`google_reconcile.py`, `google_sync.py`, `oauth.py`, `sync_store.py`,
`telephony.py`, `tools.py`). Eng keskin joy — **`engine.py` dagi tasdiq muddati**.

## 94. Muammo: bir faktni ikki xil yo'l o'qiydi

Tasdiq yaratilganda muddat `now + 86400` qilib yoziladi. Keyin **ikki mustaqil**
kod yo'li shu bitta muddatni so'raydi:

```python
# 431-qator — approve: qaror QABUL QILINADIMI (inson harakati)
if r['status']!='pending' or r['step_status'] not in {'queued','waiting_approval'} \
        or r['expires']<=self.clock():
    raise Conflict('Approval already decided, expired or step not pending')

# 474-qator — claim/tick: BAJARILADIMI (worker yo'li)
if not a or a['fingerprint']!=r['fingerprint'] or a['expires']<=now:
    c.execute("UPDATE p_steps SET status='failed',error='approval_expired_or_invalid' ...")
```

Ikkalasi **ikki xil metodda, ikki xil literal**. Ularni bog'laydigan hech narsa
yo'q: tip tizimi ham, umumiy konstanta ham. Agar ular **bir sekundga** ajralib
qolsa:

- qaror **qabul qilinadi**, lekin keyin **hech qachon bajarilmaydi** — tasdiq
  `consumed` bo'ladi, qadam abadiy kutadi; yoki
- aksi — qaror rad etiladi, lekin qadam **bajariladi**.

## 95. Nega mavjud sinov buni ushlamaydi

`test_approval_expires` soatni **`+86401`** — chegaradan **bir sekund nariga**
suradi. O'sha nuqtada ham `<=`, ham `<` rad etadi:

| holat | `<=` rad etadimi | `<` rad etadimi | ajratadimi |
|---|---|---|---|
| `deadline - 1` | yo'q | yo'q | **yo'q** |
| `deadline` | **ha** | yo'q | **ha** |
| `deadline + 1` | ha | ha | **yo'q** |

Ya'ni u "qayerdadir rad etish bo'ladi"ni isbotlaydi, **chegara qayerda** ekanini
emas. IX, XI va XIII fazalarda o'rganilgan qoida takrorlandi: **chegaradagi
sinov chegaradan uzoqda tursa — u bezak.**

## 96. Uchta sinov qo'shildi (57 → 60)

```python
def test_approval_deadline_second_is_expired(self):
    # deadline-1: tasdiq TURIshI kerak (aks holda sinov "hammani rad etadi" bilan o'tadi)
    t=self.write(); sid=...; deadline=self.now+86400; self.now=deadline-1
    self.e.approve('a',sid,'operator','approved','operator'); self.e.tick('a')
    self.assertEqual('succeeded', self.e.get('a',t)['status'])

def test_approval_at_the_deadline_second_cannot_be_decided(self):
    self.now+=86400
    with self.assertRaises(Conflict): self.e.approve(...)
    # QAROR rad etilgach qator TEGILMAGAN bo'lishi shart (quyida, 97-band)
    ...

def test_approval_at_the_deadline_second_cannot_execute(self):
    # faqat tick yo'lini so'raydi: avval 'approved' yoziladi, keyin soat
    # AYNAN chegaraga suriladi
    ...
    self.assertEqual('approval_expired_or_invalid', row[0])
```

## 97. Adversarial topilma: `assertRaises(Conflict)` yetarli emas

Dastlab `test_..._cannot_be_decided` **"ajratmaydi"** degan xulosaga keldim —
qaytarish (revert) paytida yashil qolgan edi. Sabab noto'g'ri o'lchov edi, lekin
izlanish haqiqiy **test zaifligini** ochdi.

`approve` dagi predikat **uch haddan** iborat: (1) `status!='pending'`,
(2) `step_status` ruxsat etilmagan, (3) muddat o'tgan. `assertRaises(Conflict)`
bo'lsa **qaysi had** otilganini aytmaydi. Yangi `write()` da (1) va (2)
o'lchab **yolg'on** ekani tasdiqlandi — demak faqat (3) qoladi. Lekin bu
**fixture xossasi**, uni **da'vo qilish** kerak, **farz qilish** emas.

Shuning uchun sinov qatorni **qayta o'qiydi**:

```python
with self.e.read() as c:
    row=c.execute('SELECT a.status,a.actor,s.status step_status FROM p_approvals a '
                  'JOIN p_steps s ON s.id=a.step WHERE a.step=?',(sid,)).fetchone()
self.assertEqual(('pending','','queued'), tuple(row),
                 'rad etilgan qaror tasdiqni tegilmagan qoldirishi shart')
```

Agar tasdiq **qabul qilingan** bo'lsa, `status='approved'` va `actor` yozilgan
bo'lardi. Uch hadli predikatda bu — **qaysi had otilganini nomlash**.

## 98. Qaytarish matritsasi (uch rejim)

Har bir saytni **yolg'iz** va **ikkalasini birga** ag'darib o'lchandi:

| ag'darilgan sayt | yiqilgan sinovlar | izoh |
|---|---|---|
| faqat `approve` (`<=` → `<`) | `..._cannot_be_decided` | qaror **qabul qilindi**, ya'ni sinov shu saytni ushlaydi |
| faqat `tick` (`<=` → `<`) | `..._cannot_be_decided` **va** `..._cannot_execute` | bajarish yo'li **o'tib ketdi** |
| ikkalasi birga | ikkalasi | — |
| **hech biri** (asl holat) | **0** — 60/60 OK | — |

Muhim: `decided` sinovi **faqat `approve` sayti ag'darilganda** yiqiladi —
ya'ni u haqiqatan **shu predikatni** qo'riqlaydi. Probe ham mustaqil
tekshiradi: ag'darilganda **3 ta FAIL**, tiklanganda **0**.

## 99. Natijalar

| Ko'rsatkich | Oldin | Keyin |
|---|---|---|
| `test_engine` | 57 | **60** |
| Probe xossalari | 181 | **193** |
| Probe bo'limlari | 15 | **16** |

## 100. XIV faza saboqlari

1. **Bitta muddat, ikki o'quvchi — ikkalasi ham o'lchanishi kerak.** Ular
   birlashganini kafolatlaydigan hech narsa yo'q; buni faqat sinov ushlaydi.
2. **`assertRaises` — kuchsiz da'vo.** Uch hadli predikatda u qaysi had
   otilganini aytmaydi. Qatorni **qayta o'qib**, "tegilmagan"ni da'vo qil.
3. **Noto'g'ri xulosam ham o'lchov natijasi edi.** "Ajratmaydi" degan xulosa
   ikki saytni birga ag'darganimdan kelib chiqdi; saytlarni **yolg'iz** ag'darish
   haqiqiy javobni berdi. **O'lchovni ajratmasang — xulosa ham aralashadi.**
4. **Har bir yangi sinov qaytarish matritsasida o'z saytini ko'rsatishi shart.**
   Aks holda u "biror narsani" o'lchaydi, o'zi kerak bo'lgan narsani emas.

## 101. XV faza — lease chegarasi: ikki shakl **to'ldiruvchi** bo'lishi shart

Keyingi qadam — egalik tokenining (`claim`) **lease** muddati. Bu XIV fazadagi
tasdiq muddatining **egizagi**, lekin xatosi **og'irroq**.

Lease ni **ikki xil metod** so'raydi:

```python
# finish (497) va dispatch fence (273) — STRICT
"... AND claim=? AND status='running' AND lease>?"     # lease > now

# claim sweep (442) va event dequeue (609) — INCLUSIVE
"... AND status='running' AND lease<=?"                # lease <= now
```

## 102. Xavfsizlik xossasi: **aniq to'ldiruvchilik**

Bu ikki shakl bir-birining **aniq to'ldiruvchisi**: har lahzada, **tenglik ham
kiradi**, **aynan bittasi** ishlaydi. Bu tasodif emas — egаlik tokenini
xavfsiz qiladigan xossa. U **ikki xil** yo'l bilan buziladi va ikkalasi ham
**jimgina**:

| Buzilish | Tenglikda nima bo'ladi | Oqibat |
|---|---|---|
| sweep **strict** (`lease < now`) | **hech biri** ishlamaydi — **bo'shliq** | qadam `running` da **o'lik claim** ostida qoladi, uni hech narsa tozalamaydi |
| finish **inclusive** (`lease >= now`) | **ikkalasi** ishlaydi — **ustma-ust** | worker sweep allaqachon `uncertain` qilib qo'ygan qadamni "tugatadi" |

## 103. Bo'shliqni **o'lchadim**, farz qilmadim

Sweep ni strict qilib ag'dardim va **oqibatini** ko'rdim — faqat assertion
yiqilishini emas:

```
CONSEQUENCE of sweep being strict at the boundary second:
  step status after a second claim attempt : {'status': 'running', 'error': '', 'claim': '7385e4bf...'}
  task status                              : running
  a second worker got the step?            : None
  -> the step stays running under a DEAD claim; nothing ever retires it.
```

Ya'ni qadam **abadiy tiqilib qoladi** — tiklash butunlay keyingi soat
surilishiga bog'liq bo'lib qoladi, ya'ni chegara bir sekundga **surilgan**.

## 104. Nega mavjud sinov buni ushlamagan

`test_stale_claim_cannot_finish` soatni **`+91`** — 90 sekundlik leasedan
**bir sekund nariga** suradi. U yerda ikkala shakl ham **rozilik bildiradi**
(lease o'lik). Ya'ni sinov chegara 90 da, 91 da yoki o'rtada joylashganidan
qat'i nazar **o'tadi**.

O'lchandi:

| surish | `finish` qabul qiladimi | sweep o'ldiradimi |
|---|---|---|
| `+89` | **ha** | yo'q |
| `+90` (**aynan chegara**) | yo'q | **ha** |
| `+91` | yo'q | ha |

Ajratuvchi yana **faqat tenglik** — IX, XI, XIII, XIV fazalardagi qoida
takrorlandi.

## 105. Uchta sinov qo'shildi (60 → 63)

```python
def test_lease_is_live_one_second_inside_the_bound(self):
    # chegara ICHI: lease hali tirik, finish qabul qilishi shart, sweep tegmasligi shart
    self.now=lease-1
    self.assertIsNone(self.e.claim('a','w2'), 'bir sekund ichida boshqa worker o‘g‘irlay olmasin')
    self.e.finish('a',s['id'],s['claim'],{'ok':True})

def test_lease_bound_is_exclusive_on_both_sides(self):
    # AYNAN tenglikda, IKKI yo'nalish ham qadaladi
    self.now=lease
    with self.assertRaises(Conflict): self.e.finish(...)      # yo'nalish 1
    self.assertIsNone(self.e.claim('a','w2'))                 # yo'nalish 2
    self.assertEqual('uncertain', ...)
    self.assertEqual('lease_expired', row[0])                 # sweep, finish emas

def test_lease_overrun_is_still_refused_further_out(self):
    # "faqat bir sekundlik hiyla" emasligini isbotlaydi
    self.now=lease+3600
```

## 106. Qaytarish matritsasi (har buzilish yolg'iz)

| ag'darilgan | yiqilgan sinovlar |
|---|---|
| sweep strict (bo'shliq) | `..._exclusive_on_both_sides` (1) |
| finish inclusive (ustma-ust) | `..._exclusive_on_both_sides` (1) |
| ikkalasi | 1 |
| **hech biri** | **0** — 63/63 OK |

Probe ham mustaqil ajratadi: sweep ag'darilsa **2 FAIL**, tiklanganda **0**.

## 107. Natijalar

| Ko'rsatkich | Oldin | Keyin |
|---|---|---|
| `test_engine` | 60 | **63** |
| Probe xossalari | 193 | **213** |
| Probe bo'limlari | 16 | **17** |
| Audit bo'limlari | 100 | **107** |
| Offline to'plam | 2392 | **2395** |
| Ochilgan risklar | 19 | **20** |

## 108. XV faza saboqlari

1. **To'ldiruvchilik — o'lchanadigan xossa, farz qilinadigan narsa emas.** Ikki
   shakl har lahza **aynan bittasi** ishlashini kod kafolatlamaydi; buni faqat
   sinov ushlaydi.
2. **Ikki xil buzilish bor: bo'shliq ham, ustma-ust ham.** Faqat bittasini
   tekshirish — yarim tekshirish.
3. **Oqibatni o'lcha, assertion yiqilishini emas.** "Sinov yiqildi" — dalil
   emas. `status='running'` + o'lik `claim` — **dalil**.
4. **Probe ning o'zi ham xato qiladi.** Bu fazada probe 5 marta yiqildi, hammasi
   **mening** xatolarim: noto'g'ri "truth" qiymatlari, takrorlangan yorliq,
   satr mosligi. Har biri o'lchov natijasi edi — va tuzatildi.
5. **`lease>=?` / `lease<?` — kodda yo'q.** Probe buni **nomlab** tekshiradi:
   "drift" sodir bo'lmaganini bilish ham dalildir.

## 109. XVI faza — event lease: **bitta** o'quvchi va jimgina tayanch xossa

`p_events` ning lease i — XV fazadagi qadam lease idan **tuzilish jihatdan
boshqa**. Avval shaklni yonma-yon qo'yamiz:

| | Qadam lease | Event lease |
|---|---|---|
| Muddat | `now + lease_seconds` (parametr) | `now + 120` (**qattiq kodlangan**) |
| Sweep / dequeue | `status='running' AND lease<=?` | `status='processing' AND lease<=?` |
| Tugatish qo'riqchisi | `claim=? AND status='running' AND lease>?` | `claim=?` — **lease yo'q** |

## 110. Asimmetriya: qadam **ikki marta** to'siladi, event — **bir marta**

Qadam tugatilishi **ham** token, **ham** muddat bilan to'siladi. Event
tugatilishi esa faqat **token** bilan. Bu **ataylab** shunday — va **xavfsiz**,
lekin faqat bitta tayanch xossa tufayli.

Avval **poyga emasligini** o'lchadim: dequeue (609) va claim (612) — ikki alohida
bayonot. Lekin `tx()` **`BEGIN IMMEDIATE`** ochadi, ya'ni yozuv qulfini
**boshidan** oladi; hech bir parallel yozuvchi ularning orasiga kira olmaydi.
Demak bu yerda poyga **yo'q**.

## 111. Tayanch xossa: **har claim yangi token yasaydi**

Event tugatilishi `... AND claim=?` bilan tugaydi. Demak: **superseded** worker
o'zi endi egasi bo'lmagan qatorni **ustiga yoza oladimi?**

**Javob: yo'q — lekin faqat token har claimda yangidan yasalgani uchun.**
Buni **adversarial o'lchadim**, farz qilmadim:

```
worker A held claim=TOKEN_A with lease=1120
after the lease passed, re-claim succeeded: True
row now: {'status': 'done', 'claim': '', 'lease': 0.0}
token changed from TOKEN_A? True
A's late UPDATE ... AND claim='TOKEN_A' would match: False
-> matches nothing; B's completed result is NOT overwritten.
```

Ya'ni **lease to'sig'ining yo'qligi token yangilanishi bilan qoplanadi**. Agar
kelajakda kimdir tokenni qayta ishlatsa — yoki tugatish qo'riqchisini tokendan
boshqa narsaga ko'chirsa — event yo'li qadam yo'lida hali ham bor **ikki
qavatli to'siqni jimgina yo'qotadi**. Bu xossa **hech qayerda da'vo
qilinmagan edi**. Endi qilinadi.

## 112. Nega mavjud sinov ushlamagan

`test_event_crash_recovery_after_submission` `lease=0` qilib qo'yadi — **~57 yil
o'tmishda**. U yerda inclusive ham, exclusive ham rozilik bildiradi. Ya'ni sinov
"tiklash bo'ladi"ni isbotlagan, **tiklash qayerdan boshlanishini** emas.

O'lchandi (muddat 120):

| `now` | `lease<=now` | `lease<now` | ajratadimi |
|---|---|---|---|
| `lease-1` | yo'q | yo'q | **yo'q** |
| `lease` | **ha** | yo'q | **ha** |
| `lease+1` | ha | ha | **yo'q** |

Ajratuvchi yana **faqat tenglik** — IX, XI, XIII, XIV, XV fazalar qoidasi.

## 113. Ikki sinov qo'shildi (63 → 65)

```python
def test_event_lease_boundary_is_the_only_recovery_instant(self):
    # chegara ichi: event hali egasi bilan, dequeue tegmasligi shart, claim saqlanadi
    self.now=lease-1
    self.assertFalse(self.e.process_event(...))
    self.assertEqual('TOKEN_A', row[0], 'asl claim tegilmagan qolishi shart')
    # aynan chegara: endi o'lik, tiklanadigan bo'lishi shart
    self.now=lease
    self.assertTrue(self.e.process_event(...))

def test_event_reclaim_mints_a_new_token(self):
    # tayanch xossa: yangi claim YANGI token yasaydi, eskisini qayta ishlatmaydi
    self.now=lease+1
    self.e.process_event(...)
    self.assertNotEqual('TOKEN_A', row['claim'], ...)
    self.assertIsNone(match, 'superseded token hech bir qatorga mos kelmasligi shart')
```

## 114. Qaytarish matritsasi

| ag'darilgan | yiqilgan sinovlar |
|---|---|
| dequeue strict (`lease<=` → `lease<`) | `..._only_recovery_instant` (1) |
| token qayta ishlatildi (hazard kiritildi) | `..._mints_a_new_token` (1) |
| **hech biri** | **0** — 65/65 OK |

Probe ham mustaqil: dequeue ag'darilsa **2 FAIL**, tiklanganda **0**.

## 115. Natijalar

| Ko'rsatkich | Oldin | Keyin |
|---|---|---|
| `test_engine` | 63 | **65** |
| Probe xossalari | 213 | **227** |
| Probe bo'limlari | 17 | **18** |
| Audit bo'limlari | 108 | **115** |
| Offline to'plam | 2395 | **2397** |
| Ochilgan risklar | 20 | **21** |

## 116. XVI faza saboqlari

1. **Hamma chegara egizak emas.** Qadam lease ida **ikki** o'quvchi bor edi va
   ular **to'ldiruvchi** bo'lishi shart edi. Event lease ida **bitta** o'quvchi
   bor — demak tekshiriladigan xossa boshqa: **yo'q to'siqning o'rnini nima
   bosadi**?
2. **Yo'q to'siq ham dalil talab qiladi.** `lease>?` ning yo'qligi xato emas, lekin
   uni **oqlaydigan** xossa o'lchanishi shart — bu yerda: token yangilanishi.
3. **Poyga bo'lmaganini ham isbotlash kerak.** Ikki bayonot orasidagi oyna
   xavfli **ko'rinadi**; `BEGIN IMMEDIATE` uni yopadi. Buni **o'qib** bilib
   bo'ladi, lekin endi **yozib** ham qo'yildi.
4. **Eng qimmatli sinov — eng ko'rinmas xossani qo'riqlaydigani.** Token
   yangilanishi bugun to'g'ri; uni hech narsa da'vo qilmasa, ertaga jimgina
   o'zgaradi.

## 117. XVII faza — inbox oynalari: **uch** chegara, **uch** xil shakl

`accept_event` **uchta** alohida limit qo'yadi va ularning **shakli boshqa-boshqa**:

| Limit | Shakl | Chegara | Samara |
|---|---|---|---|
| **Kunlik kvota** | `(tenant, day)` hisoblagich | `count>=10000` | kuniga **aniq 10000** qabul |
| **Backpressure** | **jonli** o'lchagich (`pending`,`processing`) | `pending>=1000` | ochiq hodisalar **aniq 1000** |
| **Kun oynasi** | `int(clock()//86400)` | — | yangi kun → **yangi** hisoblagich |

## 118. Kvota off-by-one — **to'g'ri**

Hisoblagich **oshirishdan OLDIN** o'qiladi:

```python
quota = SELECT count FROM p_quota WHERE tenant=? AND day=?     # 599 dan oldin
if pending>=1000 or (quota and quota['count']>=10000): raise   # 599
INSERT ... ON CONFLICT DO UPDATE SET count=count+1             # 600
```

Ya'ni `count==9999` da qabul qilinadi va `10000` bo'lib yoziladi; `10001`-si
`count==10000` ni ko'rib rad etadi. **Samarali chegara — aniq 10000.** O'lchandi:

| boshlang'ich | natija | yozildi |
|---|---|---|
| `9998` | qabul | 9999 |
| `9999` | **qabul** | **10000** |
| `10000` | `RateLimited` | 10000 |

## 119. Mavjud sinov — bu safar **haqiqatan** ajratadi

`test_inbox_quota_atomic_and_replay_not_charged` `count=10000` ni **to'g'ridan
to'g'ri** yozadi. Ya'ni u **chegaraning ustida** turadi va `>=` ni `>` dan
**ajratadi** — lease/tasdiq hollaridan farqli. Lekin u faqat **tashqarisini**
qadaydi:

| Nima | Holat |
|---|---|
| tashqarisi (`10000` → rad) | ✅ qadalgan |
| **ichkari** (`9999` → qabul va **aniq 10000**) | ❌ **yo'q** |
| **backpressure** (`pending>=1000`) | ❌ **umuman yo'q** |
| **kun oynasi** (yangi kun → yangi hisoblagich) | ❌ **yo'q** |

## 120. Uchta sinov qo'shildi (65 → 68)

```python
def test_quota_inside_the_bound_is_accepted_and_lands_exactly_on_it(self):
    # 9999 -> qabul VA aniq 10000; keyin 10000 -> rad, hisoblagich qimirlamaydi
    with self.e.tx() as c: c.execute('UPDATE p_quota SET count=9999')
    self.e.accept_event('a','web','next',{'text':'y'})
    self.assertEqual(10000, ...)
    with self.assertRaises(RateLimited): self.e.accept_event('a','web','over',...)
    self.assertEqual(10000, ...)   # rad etilgan accept kvotani sarflamaydi

def test_outstanding_backpressure_bound_is_exclusive_at_the_cap(self):
    # 999 ochiq -> 1000-chisi QABUL; keyin 1001-chisi RAD
    for i in range(999): INSERT ... status='pending'
    self.e.accept_event('a','web','the-1000th',...)
    with self.assertRaises(RateLimited): self.e.accept_event('a','web','the-1001st',...)

def test_quota_window_resets_on_a_new_day_index(self):
    # eski kun to'la -> rad; yangi kun -> yangi qator, qabul
    self.now=base-1   # oxirgi sekund
    with self.assertRaises(RateLimited): self.e.accept_event('a','web','old',...)
    self.now=base     # birinchi sekund
    self.e.accept_event('a','web','new',...)
    self.assertEqual({19999:10000, 20000:1}, rows)
```

## 121. Tartib ham xossa: rad etilgan accept **bepul**

Guard (599) **oshirishdan oldin** ko'tariladi, ya'ni rad etilgan accept kvotani
**sarflamaydi**. Bu muhim: aks holda chaqiruvchi **bitta yaroqsiz kalitni qayta
urib** butun kvotani tugatib qo'yishi mumkin edi. O'lchandi:

```
quota before conflicting replay: 1   after: 1   charged? False
```

## 122. Qaytarish matritsasi

| ag'darilgan | yiqilish |
|---|---|
| kvota `>=` → `>` | 3 (`..._atomic...`, `..._inside...`, `..._window...`) |
| backpressure `>=` → `>` | 1 (`..._backpressure...`) |
| kun indeksi `+1` surildi | 1 (`..._window...`) |
| **hech biri** | **0** — 68/68 OK |

Probe ham har bir chegarani mustaqil ajratadi: kvota → **2 FAIL**, backpressure →
**2 FAIL**, tiklanganda **0**.

## 123. Natijalar

| Ko'rsatkich | Oldin | Keyin |
|---|---|---|
| `test_engine` | 65 | **68** |
| Probe xossalari | 227 | **242** |
| Probe bo'limlari | 18 | **19** |
| Audit bo'limlari | 116 | **123** |
| Offline to'plam | 2397 | **2400** |
| Ochilgan risklar | 21 | **22** |

## 124. XVII faza saboqlari

1. **Har bir chegara "chegara sinovi" talab qilmaydi — lekin har biri
   *o'lchov* talab qiladi.** Bu fazada **xato topilmadi**; topilgan narsa —
   **ikki chegara umuman o'lchanmagan** va bittasining ichi ochiq qolgan.
   "Kod to'g'ri ko'rinadi" — dalil emas.
2. **`>=` va `>` farqi har doim ham "chegarada sinov" degani emas** — bu safar
   mavjud sinov **haqiqatan** ajratdi, chunki fixture **aynan** chegarada edi.
   Qoida o'zgarmadi: **ajratuvchi — tenglik**; faqat fixture bu safar u yerda edi.
3. **Tartib ham xossa.** `raise` **`INSERT` dan oldin** turishi — "rad etilgan
   accept bepul" degan xulqni beradi. Buni **yozib qo'yish** kerak, chunki
   satrlarni almashtirish uni jimgina buzadi.
4. **Uch xil shakl — uch xil sinov.** Hisoblagich, jonli o'lchagich va oyna
   bir-birini almashtirmaydi; har biri o'z sinovini talab qiladi.

---

## §125. Fazza yigirmanchi — qurilma kaskadi va `generation` panjarasi

### §125.1. Sirt va o'quvchilar

`engine.device(tenant, device, revoked, actor)` — qurilma ro'yxatdan chiqarilganda
unga bog'langan **uchta** oqimni bir vaqtda to'xtatadi:

1. **Bosqichlar** (satr 575): `queued` / `waiting_approval` / `running`.
2. **Tasdiqlar** (satr 571-574): `pending` / `approved`, faqat yuqoridagi
   to'xtatilayotgan bosqichlarga tegishli.
3. **Qurilmaning o'zi** (satr 577): `revoked` yoziladi va `generation` birga
   oshiriladi.

Ilova qatlamida esa (205-satr atrofi, `platform_api.py:231`):

```python
if not d or d['revoked'] or d['generation']!=claims['generation']:
    raise Forbidden('Device revoked')
```

### §125.2. O'lchangan xossa — kaskad **tor**

Kaskad **faqat** uchta "uchayotgan" holatni qamrab oladi:

| Holat | Natija | Izoh |
|---|---|---|
| `queued` | → `cancelled` | hali boshlanmagan |
| `waiting_approval` | → `cancelled` | kutayotgan |
| `running` | → `uncertain` | **natija noma'lum** — bu to'g'ri |
| `succeeded` | **tegilmaydi** | yakunlangan |
| `failed` | **tegilmaydi** | yakunlangan |
| `cancelled` | **tegilmaydi** | allaqachon to'xtatilgan |
| `uncertain` | **tegilmaydi** | allaqachon noma'lum |

`running → uncertain` tanlovi **muhim**: ish allaqachon boshlangan bo'lishi
mumkin, shuning uchun uni `cancelled` deb belgilash **yolg'on** bo'lardi —
qurilma ishni bajarib qo'ygan bo'lishi mumkin. `uncertain` — yagona halol javob.

### §125.3. O'lchangan xossa — tasdiq kaskadi **alohida to'plam**

Tasdiq kaskadi bosqich kaskadidan **boshqa** to'plamdan foydalanadi:
`pending`, `approved`. `consumed` **tegilmaydi**.

Bu ikkalasi bir xil ko'rinadi, lekin **bir xil emas**. Tasdiqning `consumed`
bo'lishi — u allaqachon ishlatilgani; uni `rejected` ga qaytarish tarixni
buzardi.

### §125.4. O'lchangan xossa — `generation` — **panjara**, hisoblagich emas

`platform_api.py:231` nesbatni **tenglik** bilan tekshiradi:

```python
d['generation']!=claims['generation']
```

Ya'ni: `revoked=0` bo'lsa ham, `generation` ko'tarilgan **lahzadan** boshlab
eski token mos kelmaydi. Bu — "fencing token" naqshi: qiymat kattami-kichikmi
emas, **mos keladimi** muhim.

Sinov buni shunday o'lchaydi: qayta chaqiruvdan keyin `generation=2`, eski
token `1` — mos kelmaydi; va `revoked` yana `0` bo'lsa ham mos kelmaydi.

### §125.5. Nuqson — **topilmadi**

Uchala xossa ham **to'g'ri**. Lekin uchalasi ham **hech qanday sinov bilan
qadalmagan edi**: kaskad torligi, tasdiq to'plamining alohidaligi va
`generation` ning panjara ekanligi — faqat kodda mavjud edi.

### §125.6. Revert matritsasi

| Buzilish | Yiqilgan sinov |
|---|---|
| `cascade_wide` (yakunlangan holatlarni ham qamrab olish) | 1 |
| `cascade_narrow` (faqat `queued` ni olish) | 2 |
| `approval_wide` (`consumed` ni ham rad etish) | 1 |
| `gen_no_bump` (`generation` ni oshirmaslik) | 2 |
| **hech narsa** | **0** (71/71 OK) |

### §125.7. Qo'shilgan sinovlar (68 → 71)

- `test_device_cascade_fences_exactly_the_in_flight_states` — butun matritsa.
- `test_device_cascade_rejects_only_undecided_approvals` — `consumed` tegilmasligi.
- `test_device_generation_is_a_fencing_token_not_a_counter` — tenglik panjarasi.

### §125.8. Saboqlar

1. **Ikki kaskad — ikki to'plam.** "Bir xil ko'rinadi" — "bir xil" emas.
   Har bir to'plam o'z sinovini talab qiladi.
2. **`uncertain` — halollik holati.** Uchayotgan ishni `cancelled` deb
   belgilash qulay, lekin yolg'on.
3. **Panjara tenglik bilan o'lchanadi.** `generation` "kattami" emas —
   "mos keladimi". Sinov ham tenglikni tekshirishi kerak.
4. **To'g'ri, lekin o'lchanmagan — tekshirilgan emas.** Bu fazza buni yana
   bir marta tasdiqladi.

---

## §126. Fazza yigirma birinchi — throttle oynasi va rad chegarasi

### §126.1. Sirt

`app/identity_store.py:80-88` — **doimiy qat'iy oyna throttle**. Parol
tekshiruvidan (`scrypt`) **oldin** ishlaydi, shuning uchun bu yerdagi xato
yo **qulflash** (juda qattiq), yo **ochiq eshik** (juda yumshoq) demakdir.

```python
def throttle(namespace, key, limit=20, window_seconds=900):
    bucket = hashlib.sha256((namespace + ':' + str(key)[:512]).encode()).hexdigest()
    window = int(time.time() // window_seconds)
    with tx() as c:
        c.execute('DELETE FROM p_auth_limits WHERE window<?', (window-2,))
        c.execute('INSERT INTO p_auth_limits VALUES(?,?,1) ON CONFLICT(bucket,window) DO UPDATE SET count=count+1', (bucket,window))
        count = c.execute('SELECT count FROM p_auth_limits WHERE bucket=? AND window=?', (bucket,window)).fetchone()[0]
    if count > limit: raise AuthRateLimited('Try again later')
```

Ikki chaqiruvchi: `authenticate` (satr 120, `login-account`, 20/900s) va
`identity_api.py:68` (`invoke(store.throttle,'http-'+kind,peer,60)`).

### §126.2. Uch mustaqil chegara

| Chegara | Predikat | Nima buziladi |
|---|---|---|
| **Rad** | `count > limit` | `>=` bo'lsa — limitdan **bitta kam** rad etiladi |
| **Oyna indeksi** | `int(now // window_seconds)` | siljish — hisoblagich soatdan uziladi |
| **Tozalash** | `DELETE WHERE window < window-2` | `<=` bo'lsa — hali kerak hisoblagich o'chadi |

### §126.3. Nega `>` to'g'ri

`count` **oshib bo'lgandan keyin** o'qiladi:

```python
INSERT ... count=count+1     # avval oshiradi
SELECT count ...             # keyin o'qiydi
if count > limit: ...        # keyin qaror
```

Demak **`limit`-chi chaqiruv o'tadi** (`count == limit`) va **`limit+1`-chi**
rad etiladi. Ya'ni `limit=3` → 3 ta o'tadi, 4-chisi rad. Bu to'g'ri.

### §126.4. Mavjud sinov nimani o'lchagan, nimani o'lchamagan

`test_rate_limit_persists_across_reconnect`:

```python
for _ in range(2): s.throttle('test','account',2)
reset()
with self.assertRaises(s.AuthRateLimited): s.throttle('test','account',2)
```

Fixture **aynan chegarada** (2-chi chaqiruv `count=2`), shuning uchun
`>` ↔ `>=` **qadalgan** — bu o'sha nodir holatlardan biri.

**Lekin:** sinov **soatni surmaydi**. `reset()` faqat ulanishni qayta ochadi.
Shuning uchun:
- **Oyna indeksi** — umuman o'lchanmagan.
- **Tozalash chegarasi** — umuman o'lchanmagan.
- **`count == limit` o'tishi** — bilvosita (rad etilmasligi), lekin **ochiq
  tasdiqlanmagan**.

### §126.5. O'lchov, dalil bilan

Soatni `patch.object(s.time,'time',...)` bilan in'ektsiya qilib o'lchandi:

**Rad chegarasi** (`limit=3`): 1 OK, 2 OK, 3 OK, 4 **DENY**. Hisoblagich
`count=4` bo'lib qoladi — **rad etilgan chaqiruv ham yoziladi**, orqaga
qaytarilmaydi.

**Oyna indeksi** (`base = 900*10000`):
- `t = base-1` → oyna `9999`
- `t = base` → oyna **`10000`** — yangi oyna, hisoblagich **noldan**
- Burilish **aynan `tick` da**, bir soniya oldin emas.

**Tozalash chegarasi** (`cur = 20000`, `window-2 = 19998`):
- Oldin: `{19996, 19997, 19998, 19999, 20000}`
- Keyin: `{**19998**, 19999, 20000}` — ya'ni **uchta** oyna qoladi
- `DELETE WHERE window < 19998` — **qat'iy `<`**, shuning uchun `19998` **qoladi**

### §126.6. Nuqson — **topilmadi**

Uchala chegara ham **to'g'ri**. Mening **o'z taxminlarim** ikki marta xato
chiqdi:
1. `base-1` ni `10000` deb o'yladim — aslida `9999` (`floor`).
2. Tozalash **ikki** oyna qoldiradi deb o'yladim — aslida **uchta**, chunki
   predikat `< window-2`, `<= window-2` emas.

Ikkalasi ham kod emas, **mening fixture arifmetikam** edi.

### §126.7. Revert matritsasi

| Buzilish | Yiqilgan sinov | Probe FAIL |
|---|---|---|
| `count > limit` → `>=` | 1 (+4 error) | 2 |
| oyna indeksi `+1` siljidi | 2 | 1 |
| tozalash `<` → `<=` | 1 | 2 |
| bucket `namespace:key` → konstanta | 1 error | — |
| **hech narsa** | **0** (35/35 OK) | **0** (272/272) |

### §126.8. Qo'shilgan sinovlar

- `test_rate_limit_bound_is_exclusive_so_the_limit_call_itself_succeeds`
  — `limit`-chi chaqiruv **o'tishi**, hisoblagich esa **qaytarilmasligi**.
- `test_window_index_advances_exactly_on_the_tick`
  — `base-1` va `base` **turli** oynada; burilish aynan `tick` da.
- `test_cleanup_retains_the_previous_window_and_drops_older_ones`
  — aynan **uchta** oyna qoladi.
- `test_throttle_buckets_do_not_share_a_counter`
  — namespace ham, key ham bucket'ga kiradi.

### §126.9. Saboqlar

1. **"Chegara sinovi bor" degani "hamma chegara sinovi bor" degani emas.**
   Bu yerda **uchta** chegara bor edi, bittasi qadalgan.
2. **Soatni surmagan sinov vaqt oynasini o'lchamaydi.** `reset()` — soat emas.
3. **`<` va `<=` ajratuvchi, `<` va `<+1` esa bir xil ko'rinadi.**
   Tozalash predikatini **nechta satr qolishini sanab** o'lchash kerak.
4. **O'z fixture arifmetikang eng ko'p xato qiladigan joy.** Ikki marta
   xato qildim; ikkalasini ham kod emas, **o'lchov** tuzatdi.

---

## §127. Fazza yigirma ikkinchi — OAuth muddat o'quvchilari

### §127.1. Sirt — **to'rtta** o'quvchi, **ikki xil** sezgi

`platform_runtime/oauth.py` kredensialni vaqt bo'yicha **to'rtta** joyda
darvozalaydi:

| # | O'quvchi | Predikat | Sezgi |
|---|---|---|---|
| 1 | **State TTL** (satr 183) | `pending['expires'] <= now` | **inklyuziv** |
| 2 | **Access headroom** (satr 246) | `row['expires'] > now+60` | **eksklyuziv** |
| 3 | **Stale lease** (satr 304, 358) | `row['started']+120 > now` | **eksklyuziv** |
| 4 | **Grant fence** (satr 270) | `row['expires'] <= now` | **inklyuziv** |

Ikkitasi `<=`, ikkitasi `>`. Ya'ni **bir xil faylda ikki qarama-qarshi
konvensiya** — va shu faylning o'zida **`120` literali ikki marta** uchraydi
(`recover_stale` va `_drain_revoke`), shuning uchun bittasini o'zgartirish
jimgina ikkinchisini ham suradi.

### §127.2. Mavjud qo'riqchilar — hammasi **uzoqdan**

| Qo'riqchi | Sakraydi | Chegara |
|---|---|---|
| `test_expired_state_denied` | **+601s** | 600 |
| `test_expiry_refresh` | **`+= 3550`** | 60 (headroom) |
| `test_stale_refresh_requires_explicit_recovery` | **+121s** | 120 |
| `test_generation_fence_blocks_pre_revoke_grant` | generation | `expires` umuman yo'q |

To'rttasi ham chegaradan **uzoqda** sakraydi. Shu sababli `<=` ↔ `<` va
`60` ↔ `61` farqlarini **hech biri** o'lchamagan.

### §127.3. O'lchov

Birinchi to'siq: `cryptography` moduli bu muhitda **yo'q edi** — `SecretVault`
`VaultError` beradi va butun `test_oauth` moduli (44 test) yiqiladi. Bu
`docs/development/LOCAL-VERIFICATION-UZ.md` da **oldindan qayd etilgan**
(149 error'ning 138 tasi shu). Boshqariladigan venv'ga `cryptography`
o'rnatildi — **shundan keyin** o'lchov mumkin bo'ldi.

O'lchangan natijalar:

**1. State TTL** (`begin` → `expires = now+600`, o'qish `<= now`):
- `t = +599` → **o'tadi**
- `t = +600` → **rad** — tenglik **allaqachon** muddati o'tgan

**2. Access headroom** (`> now+60` → qayta ishlatish):
- `expires-now = 61` → **REUSE** (provider chaqirilmaydi)
- `expires-now = 60` → **refresh** (`60 > 60` yolg'on)
- `expires-now = 59` → **refresh**

**3. Stale lease** (`started+120 > now` → rad):
- `elapsed = 119` → **REFUSED** (hali uchayotgan)
- `elapsed = 120` → **ALLOWED** (muddati o'tgan)
- `elapsed = 121` → **ALLOWED**

**4. Grant fence** (`expires <= now` → rad):
- `expires-now = +1` → **o'tadi**
- `expires-now = 0` → **REJECTS** — tenglik **o'lik**
- `expires-now = -1` → **REJECTS**

### §127.4. Nuqson — **topilmadi**

To'rttasi ham **to'g'ri**. Lekin **to'rttasi ham** chegarada qadalmagan edi.

### §127.5. Revert matritsasi

| Buzilish | Yiqilgan sinov | Probe FAIL |
|---|---|---|
| state `<=` → `<` | 1 | 2 |
| headroom `+60` → `+61` | 1 | 2 |
| stale `120` → `121` (**ikki** nusxa) | 1 | 2 |
| fence `<=` → `<` | 1 | 1 |
| **hech narsa** | **0** (49/49 OK) | **0** (285/285) |

### §127.6. Qo'shilgan sinovlar (44 → 49)

- `test_state_ttl_bound_is_the_exact_instant_of_expiry`
- `test_token_headroom_bound_is_sixty_seconds_exclusive`
- `test_fence_rejects_at_the_expiry_instant_not_after`
- `test_stale_attempt_bound_is_exclusive_at_one_twenty`

### §127.7. Uchta **o'zim qilgan** xato

Birinchi yurgizishda 4 tadan **3 tasi** yiqildi — hammasi mening harness'im:
1. `begin()` ikkinchi marta `active` ulanish ustida ishlamaydi
   (`Disconnect active or in-flight OAuth connection`) — orasida `revoke`
   kerak.
2. Refresh saqlangan `expires` ni **oldinga suradi**, shuning uchun uni har
   raundda **qayta o'qish** kerak.
3. `recover_stale` generatsiyani ko'taradi va envelopeni tozalaydi — keyingi
   raund **yangi** to'liq oqim talab qiladi.

Uchtasi ham kod emas, **o'lchov apparati** edi.

### §127.8. Saboqlar

1. **Bir faylda ikki qarama-qarshi konvensiya bo'lishi mumkin.** `<=` va `>`
   yonma-yon turadi; "fayl izchil" deb faraz qilish xato.
2. **Bitta literal ikki joyda — ikki barobar xavf.** `120` ni bir joyda
   o'zgartirish ikkinchisini ham suradi; probe **sonini** tekshiradi.
3. **"Uzoqdan sakraydigan" qo'riqchi chegara haqida hech narsa demaydi.**
   To'rttasi ham shunday edi.
4. **Yo'q kutubxona "test yiqildi" emas — "o'lchov bo'lmadi".**
   `cryptography` yo'qligi 138 error bergan edi; o'rnatilgach chegara
   **birinchi marta** ko'rindi.

---

## §128. Fazza yigirma uchinchi — escalation va reengagement vaqt oynalari

### §128.1. Sirt — **ikki modul, bir xil shakl**

`escalation.py` (kechikkan ish → menejer) va `reengagement.py` (to'xtab qolgan
lid → mijoz) — **ikki xil mahsulot, bir xil uchta chegara**:

| Chegara | Predikat | Sezgi |
|---|---|---|
| **Cooldown** (esc 349, re 224) | `last_attempt + cooldown > now` → jim | **eksklyuziv** |
| **Due o'qish** (esc 612, re 353) | `... AND next_due <= clock` | **inklyuziv** |
| **Interval surish** (`_advance`) | `next_due = now + interval` | — |

### §128.2. Mavjud qo'riqchilar — hammasi **bir tomonlama**

| Qo'riqchi | Sakraydi | Chegara |
|---|---|---|
| `test_only_a_queued_row_is_repeatable` | `sent` holati | cooldown **emas** — u boshqa sababdan terminal |
| `test_second_cycle_inside_cooldown...` | **700** | 7200 |
| `test_cooldown_expiry_allows_exactly_max_attempts` | **3601** | 3600 |
| `test_nothing_is_delivered_before_the_schedule_is_due` | `next_due` kelajakda | tenglik **emas** |
| `test_tick_without_due_policy_does_nothing` | interval narisida | tenglik **emas** |

Ya'ni cooldown uchun **hech bir** sinov tenglikni o'lchamagan, due o'qish uchun
esa **hech bir** sinov faqat bir tomonni ko'rgan.

### §128.3. O'lchov

**Cooldown** (`cooldown = 3600` / `86400`):

| O'tgan vaqt | Escalation | Reengagement |
|---|---|---|
| −1s (3599 / 86399) | **SILENT** | **SILENT** |
| **aynan chegara** | **RETRY** | **RETRY** |
| +1s (3601 / 86401) | RETRY | RETRY |

**Due o'qish:** `next_due == now` → **due** (tick `True`); `next_due == now+1`
→ **due emas** (tick `False`). Ikkala modulda ham.

**Interval surish:** tick'dan keyin `next_due − last_run` = **aynan** `interval`
(3600 / 600) — ikki barobar emas, nol emas.

### §128.4. Nuqson — **topilmadi**

Oltala nuqta ham **to'g'ri**. Lekin **oltalasi ham** chegarada qadalmagan edi.

**Nega bu muhim:** escalation — **muvaffaqiyatsiz** yetkazishni qayta uradi;
reengagement — **tirik mijozga** qayta yozadi. Bir soniyalik siljish kimga
qachon murojaat qilinishini o'zgartiradi.

### §128.5. Revert matritsasi

| Buzilish | Yiqilgan sinov | Probe FAIL |
|---|---|---|
| esc cooldown `>` → `>=` | 2 | 2 |
| esc due `<=` → `<` | 2 | 2 |
| re cooldown `>` → `>=` | 1 | 2 |
| re due `<=` → `<` | 1 | 2 |
| **hech narsa** | **0** (153/153) | **0** (300/300) |

### §128.6. Qo'shilgan sinovlar

**`test_escalation.py`** (115 → 118):
- `test_cooldown_bound_is_the_exact_retry_instant`
- `test_a_schedule_exactly_on_its_next_due_is_due`
- `test_the_interval_advance_lands_exactly_one_interval_ahead`

**`test_reengagement.py`** (32 → 35):
- `test_cooldown_bound_is_the_exact_retry_instant`
- `test_a_policy_exactly_on_its_next_due_is_due`
- `test_the_interval_advance_lands_exactly_one_interval_ahead`

### §128.7. Saboqlar

1. **Bir xil shakl ikki modulda — ikki barobar qadalishi kerak.** Ular
   bir-biridan mustaqil rivojlanadi; bittasini tuzatish ikkinchisini
   tuzatmaydi.
2. **Bir tomonlama qo'riqchi — yarim qo'riqchi.** `sent` holati cooldown'ni
   **umuman** o'lchamaydi; u boshqa sababdan terminal.
3. **Intervalni satrdan o'qib o'lchash kerak.** `next_due − last_run` ni
   **qayta o'qish** ikki barobarlik va nolni ochadi; qo'riqchi esa ularni
   ko'rmaydi.
4. **Oltita nuqta, oltita reverting.** Har biri **yakka** o'lchandi; hech biri
   ikkinchisining natijasiga tayanmadi.

---

## §129. Fazza yigirma to'rtinchi — oversight oynalari va supervisor chegaralari

### §129.1. Sirt — **ikki modul, to'qqizta chegara**

`oversight.py` (298 satr) va `supervisor.py` (528 satr) — agent nazorati va
savol marshrutlash. Ikkalasi ham nazorat tekisligi, ikkalasi ham **chegaralar**
bilan. To'qqizta chegara tekshirildi:

| # | Chegara | Joy | Shakl |
|---|---|---|---|
| 1 | faoliyat oynasi boshi | `oversight.py:115,119,130` | `created >= since` — **inklyuziv** |
| 2 | `since_seconds` quyi chegarasi | `oversight.py:91` | `1..MAX` — **inklyuziv** |
| 3 | `cost()` oynasi | `oversight.py:164` | `_window` orqali **meros** |
| 4 | `max_seconds` oynasi | `supervisor.py:299` | `60..86400` — **yopiq** |
| 5 | `max_steps` shifti | `supervisor.py:283` | `1..12` |
| 6 | `max_hops` shifti | `supervisor.py:281` | `1..3` |
| 7 | `history` kesilishi | `supervisor.py:420` | `total > len(rows)` |
| 8 | `_match` tenglik yechimi | `supervisor.py:254` | `(len(kw), -index)` |
| 9 | `_match` nomzod tartibi | `supervisor.py:208` | `ORDER BY id` |

### §129.2. Mavjud qo'riqchilar — **sakkiztasi chegaradan uzoqda**

| Qo'riqchi | Sakraydi | Chegara | Sezuvchan? |
|---|---|---|---|
| `test_activity_respects_the_reported_window` | oyna=1s, satrlar 29–59s eski | 1000 | **yo'q** — `>` ham bo'sh qaytaradi |
| `test_activity_window_is_bounded` | `0`, `MAX+1` | — | faqat **mavjudlik** |
| `test_the_step_budget_is_bounded` | `0`, `99` | 12 | **yo'q** — 99 > 12 |
| `test_the_hop_cap_is_bounded` | `0`, `99` | 3 | **yo'q** — 99 > 3 |
| `test_the_seconds_budget_is_bounded` | `5` | 60 | **yo'q** — 5 < 60 |
| `test_longest_keyword_wins...` | ikkala nomzod bir bo'limda | — | **yo'q** — `assertIn` ikkalasini qabul qiladi |
| `test_the_hop_count_survives_a_restart` | — | — | durable, chegara **emas** |
| `test_an_exact_fit_history_is_not_reported_as_cut` | 2 satr, limit 2 | — | **HA** ✓ |

Ya'ni **to'qqiztadan faqat bittasi** — `history` kesilishi — haqiqatan
chegarada qadalgan edi. Qolgan **sakkiztasi to'g'ri, lekin o'lchanmagan**.

### §129.3. O'lchov — 42 ta da'vo

| O'lchov | Natija |
|---|---|
| Oyna boshi `now - since_seconds` | **900.0** ✓ |
| `900.0` da yaratilgan satr oynada | **bor** (`>=`) |
| Xuddi shu so'rov `>` bilan | **yo'q** — ya'ni tenglik **ajratuvchi** |
| `since_seconds` 1 / `MAX` qabul | **ha, ha** |
| `since_seconds` 0 / `MAX+1` rad | **ha, ha** |
| `since_seconds=True` rad (`type is not int`) | **ha** |
| Uchta o'qish ham chegarada bir xil | **ha** |
| `cost.since == activity.since` | **ha** |
| `max_steps` 1 / 12 qabul, 0 / 13 rad | **ha** |
| `max_hops` 3 da cap ishlaydi | **ha** |
| `max_seconds` 60 / 86400 qabul, 59 / 86401 rad | **ha** |
| `history` aniq mos → `truncated=False` | **ha** |
| Teng uzunlikdagi kalit so'z → **alifbo** bo'yicha birinchi id | **ha** |

### §129.4. Nuqson — **ha, ikkitasi**

**Nuqson 1 — hujjat kodga zid (haqiqiy xato).**

`_routable` docstring i **"in declaration order"** der edi, `_match` docstring i
esa **"then declaration order"**. Kod esa `ORDER BY id` ishlatadi — ya'ni
**alifbo tartibi**, e'lon tartibi **emas**.

O'lchandi: `zulu` **birinchi** e'lon qilinib, `alpha` **ikkinchi**; teng
uzunlikdagi kalit so'zda g'olib — **`alpha`**. Ya'ni birinchi e'lon qilingan
**yutqazdi**.

**Nega bu xavfli:** operator hujjatni o'qib, ustuvorlik tartibida bo'lim e'lon
qiladi va **teskari** marshrut oladi — jurnal satri esa mutlaqo to'g'ri
ko'rinadi. Xato jimgina.

Tuzatildi: ikkala docstring ham haqiqiy qoidani aytadi.

**Nuqson 2 — o'zimning sinovim ko'r edi (o'z-o'zini audit).**

Birinchi urinishda `max_seconds` chegarasini `(59, 60, 86400, 86401)` bilan
o'lchadim. Revert matritsasi **M5** (`86400 → 86401`) **YASHIL** qoldi — chunki
86401 shunchaki qonuniy qiymatga aylandi va **hech narsa uni rad etishini**
talab qilmadi. Sinovni bir qadam **tashqariga** suradim: endi `86402` ham
sinaladi va manbadagi literal ham tasdiqlanadi. Shundan keyin M5 **QIZIL**.

Bu fazza davomida o'zim qilgan **uchinchi** shu turdagi xato.

### §129.5. Revert matritsasi

| Buzilish | Test suite | Probe FAIL |
|---|---|---|
| oversight task+run o'qishi `>=` → `>` | 2 fail | 2 |
| oversight audit o'qishi `>=` → `>` | 1 fail | — |
| oversight quyi chegara `1` → `2` | 4 error | — |
| supervisor tenglik yechimi `-index` → `+index` | 1 fail | 2 |
| supervisor `max_seconds` `86400` → `86401` | 1 fail | 2 |
| supervisor `MAX_STEPS_CEILING` `12` → `13` | 1 fail | 1 |
| supervisor `MAX_HOPS_CEILING` `3` → `4` | 1 fail | — |
| **hech narsa** | **0** (98/98) | **0** (334/334) |

Yetti rejimning **hammasi** qizil; qaytarilgan holat yashil.

### §129.6. Qo'shilgan sinovlar

**`test_oversight.py`** (34 → 38):
- `test_the_window_start_is_inclusive_so_a_row_on_it_is_counted`
- `test_all_three_activity_reads_share_the_inclusive_predicate`
- `test_cost_reports_the_same_window_activity_does`
- `test_a_longer_window_admits_rows_a_shorter_one_excludes`

**`test_supervisor.py`** (56 → 60):
- `test_the_hop_ceiling_itself_is_an_accepted_cap`
- `test_the_step_ceiling_itself_reaches_the_run`
- `test_the_seconds_window_edges_are_the_documented_pair`
- `test_equal_length_keywords_resolve_by_section_id_not_declaration_order`

To'liq to'plam: **2420 → 2428** sinov, imzo o'zgarmadi
(`failures=1, errors=11, skipped=1`).

### §129.7. Saboqlar

1. **Hujjat ham o'lchanishi kerak.** `ORDER BY id` va "declaration order" —
   ikkalasi ham "birinchi" deydi, lekin **boshqa** birinchisini. Operator
   hujjatga qarab qaror qabul qiladi, shuning uchun hujjat **shartnoma**.
2. **`99` shiftni isbotlamaydi.** `0` va `99` — bu "chegara bor", "chegara
   mana bu" **emas**. Chegaraning **o'zi** va undan **bir qadam** narisi sinalishi
   kerak.
3. **Chegaradan bir qadam narisi — shiftning o'zidan bir qadam narisi emas.**
   M5 xatosi: `86401` ni rad etishni talab qilmadim, shuning uchun shift
   `86401` ga surilganda sinov ko'rmadi.
4. **Bitta literal, uchta o'qish.** `created>=?` uch joyda takrorlanadi; bittasi
   `>` bo'lsa, eng yangi hodisalar jimgina yo'qoladi, vazifa soni esa to'g'ri
   qoladi. Uchalasi **birgalikda** chegarada sinalishi kerak.
5. **Merоs olingan chegara ko'rinmas.** `cost()` o'z chegarasiga ega emas —
   `_window` dan oladi. Faqat `activity` ni sinash `cost` ni qo'riqlamaydi.
6. **Test yozgach, uni sindirib ko'r.** O'z sinovimning ko'rligini faqat revert
   matritsasi ochdi. Yashil sinov — o'lchangan sinov emas.

---

## §130. Fazza yigirma beshinchi — ERP posting chegaralari

### §130.1. Sirt — **o'n bitta** chegara, ikkitasi qadalgan

`platform_runtime/erp.py` (889 satr) — platformadan moliyaviy tizimga chiqadigan
**yagona yozuv**. Modul o'zini **o'n bitta** son yoki tuzilma chegarasi bilan
qo'riqlaydi:

| # | Chegara | Shakl |
|---|---|---|
| 1 | `MAX_RESPONSE_BYTES = 80_000` | `len(raw) > MAX` |
| 2 | `MAX_NOTE_CHARS = 500` | `_text(note, ..., 500)` |
| 3 | `MAX_AMOUNT_MINOR = 10 ** 15` | `0 <= v <= MAX` |
| 4 | `timeout_seconds` | `1..60`, `type is int` |
| 5 | `_real_date` yil | `1900 <= y <= 2999` |
| 6 | `_real_date` oy / kun | `1..12`, `1..31`, `monthrange` |
| 7 | `_dig` chuqurligi | `depth > 3` |
| 8 | alias regex | `{0,63}` |
| 9 | register target | `len(target) > 64` |
| 10 | `_status_tool` clamp | `min(max(1, limit), 200)` |
| 11 | `ledger_page` kesilishi | `total > len(rows)` |

### §130.2. Mavjud qo'riqchilar — **to'qqiztasi chegaradan uzoqda**

| Qo'riqchi | Nimani o'lchaydi | Chegarani? |
|---|---|---|
| `test_a_negative_amount_is_refused` | `-1` rad | **yo'q** — shift qiymati emas |
| `test_a_float_amount_is_refused...` | tur | **yo'q** |
| `test_an_ambiguous_date_is_refused...` | `10.01`, `01.10`, `2026-13-01` | qisman |
| `test_a_disambiguated_numeric_date...` | `13.01`, `25.12` | qisman |
| `test_status_limit_is_coerced_not_typed` | `None`, `'many'` | **yo'q** — `1`/`200` emas |
| `test_status_is_not_truncated...` | aynan `limit` | **HA** ✓ |

Ya'ni **o'n bittadan faqat bittasi** — `ledger_page` kesilishi — haqiqatan
chegarada qadalgan edi. Bu III fazada topilgan o'sha chegara.

### §130.3. O'lchov

Har bir chegara ±1 qadamda **haqiqiy chaqiruv** bilan o'lchandi (54 da'vo).
Hammasi **to'g'ri** chiqdi:

- `total_minor`: `0` ✓, `10**15` ✓, `10**15+1` rad, `-1` rad, `True` rad, `1.0` rad
- matn: `200` ✓ / `201` rad; izoh: `500` ✓ / `501` rad
- `_date_text`: `13.01.2026` → `2026-01-13`, `01.13.2026` → `2026-01-13`,
  `12.12.2026` → **rad**, `13.13.2026` → **rad**
- `_real_date`: `1900`/`2999` ✓, `1899`/`3000` rad, `2024-02-29` ✓, `2023-02-29` rad
- `_dig`: 4 segment ✓, 5 segment → default
- `timeout`: `1`/`60` ✓, `0`/`61` rad
- javob baytlari: `80_000` ✓, `80_001` rad
- `limit` clamp: `1`→1, `200`→200, `0`→1, `201`→200

### §130.4. Nuqson — **bitta**, va u xabar bilan hujjat o'rtasida

**Nuqson: rad etish xabari o'sha kiritma allaqachon bajargan shartni talab qilardi.**

`_date_text('13.13.2026')` shunday rad etardi:

```
doc_date '13.13.2026' is ambiguous: both parts could be a month. ...
Send an ISO date (YYYY-MM-DD) or a form where one part is greater than 12
```

Ikkala qism ham 12 dan **katta**, ya'ni **hech biri oy bo'la olmaydi** — bu
"noaniq" emas, **imkonsiz** o'qish. Va taklif qilingan chora ("bir qismi 12 dan
katta bo'lgan shakl yuboring") aynan shu kiritmada **allaqachon bajarilgan**.
Operator aylana bo'ylab yuborilardi.

Xuddi shu nuqson hujjatda ham bor edi: docstring "**at least one** part is greater
than 12" der edi, o'lchangan qoida esa "**exactly one**".

Tuzatildi: uchinchi shox `elif first > 12:` ajratildi va **boshqa** xabar beradi
(`names no month`), docstring esa haqiqiy qoidani aytadi.

### §130.5. Revert matritsasi — 12 rejim

Birinchi yurgizishda (qadalishdan **oldin**):

| Buzilish | Sinov to'plami |
|---|---|
| M1 `MAX_RESPONSE_BYTES` `80_000` → `80_001` | **YASHIL** |
| M2 `MAX_NOTE_CHARS` `500` → `501` | **YASHIL** |
| M3 `MAX_AMOUNT_MINOR` `10**15` → `10**15+1` | **YASHIL** |
| M4 timeout yuqori `60` → `61` | **YASHIL** |
| M5 `_real_date` `2999` → `3000` | **YASHIL** |
| M6 `_dig` `> 3` → `> 4` | **YASHIL** |
| M7 alias `{0,63}` → `{0,64}` | **YASHIL** |
| M8 target `> 64` → `> 65` | **YASHIL** |
| M9 clamp `200` → `201` | **YASHIL** |
| M10 `ledger_page` `>` → `>=` | **QIZIL** (III faza) |
| M11 `first > 12` → `> 13` | **QIZIL** (bir tomonlama) |

**To'qqizta yashil mutatsiya — to'qqizta qadalgan chegara yo'q.** Tuzatishdan
keyin, o'n ikkisi ham (M12: `elif first > 12` shoxini o'chirish) **QIZIL**,
qaytarilgan holat **YASHIL**.

### §130.6. Qo'shilgan sinovlar (56 → 68)

- `test_the_boundary_constants_are_the_documented_literals` — **qator bo'yicha**
- `test_the_amount_ceiling_itself_is_accepted_and_one_past_it_is_not`
- `test_the_supplier_text_ceiling_is_two_hundred_characters`
- `test_the_note_ceiling_is_five_hundred_characters`
- `test_the_response_byte_ceiling_is_eighty_thousand`
- `test_the_timeout_window_is_one_to_sixty_seconds`
- `test_the_calendar_year_window_is_1900_to_2999`
- `test_a_leap_day_is_a_real_date_and_a_common_year_twenty_ninth_is_not`
- `test_a_numeric_date_is_unique_only_when_exactly_one_part_exceeds_twelve`
- `test_the_pointer_depth_ceiling_is_four_segments`
- `test_the_operator_alias_and_target_ceilings_are_sixty_four_characters`
- `test_the_status_limit_ceiling_is_two_hundred_rows`

Probe: **334 → 374** xossa.

### §130.7. Uchta **o'zim qilgan** xato

1. **O'ldirilgan revert yurgizishi manbani mutatsiyalangan holda qoldirdi.**
   Foreground timeout bilan to'xtatilgan harness `erp.py` ni M11 holatida
   tashlab ketdi. Keyingi yurgizish **o'sha mutatsiyalangan faylni baseline deb
   o'qidi** va uni "bayt-aynan" qaytardi — **noto'g'ri baytlarga**. Endi harness
   baseline'ni hech qachon yozilmaydigan fayldan o'qiydi, har mutatsiyadan oldin
   hash'ni tekshiradi va SIGTERM'da ham qaytaradi.
2. **Literal tekshiruvi qatorga bog'lanmagan edi.** `'MAX_AMOUNT_MINOR = 10 ** 15'`
   — `'... = 10 ** 15 + 1'` ning **qismi**. Shuning uchun M3 yashil qoldi. Endi
   tekshiruv **aniq qator a'zoligi** (`literal in set(SOURCE.splitlines())`).
   Bu §129 dagi M5 sabog'ining **ikkinchi** uchrashuvi.
3. **`if __name__` bloki ostiga qo'shilgan sinov yig'ilmaydi.** Fayl
   `if __name__ == '__main__': unittest.main()` bilan tugasa, oxiriga qo'shilgan
   indented metod **o'sha blokning ichiga** tushadi: fayl parse bo'ladi,
   discovery `OK` deydi, sinov esa **hech qachon yig'ilmaydi**. Fazza 26 da bir
   marta sodir bo'ldi va "yashil" degan xato xulosaga bir qadam qoldi.

### §130.8. Saboqlar

1. **Moliyaviy yozuvning chegaralari ham qadalishi kerak.** "Test bor" —
   "chegara o'lchangan" emas: `-1` rad etilishi shiftning **qiymatini** aytmaydi.
2. **Xabar — shartnoma.** Operator xabarni o'qib harakat qiladi; o'sha kiritma
   allaqachon bajargan chorani taklif qilish — aylana.
3. **Harness manbani buzmasligi shart.** O'ldirilgan yurgizish keyingi
   yurgizishni **jimgina** noto'g'ri qildi.
4. **Qatorga bog'lanmagan `assertIn` — qadalgan chegara emas.**
5. **Fayl oxiridagi `__main__` bloki — sinov qabri.**

---

## §131. Fazza yigirma oltinchi — ifodalab bo'lmaydigan sonlar

### §131.1. Sirt — **beshta** joy, **bir xil** shakl

Audit modul bo'yicha emas, **nuqson shakli** bo'yicha supurdi. Shakl — bitta
qator: hujayrani o'qishda `float(value)` chaqiriladi, natija mavjudmi deb
so'ramasdan. Beshta joy uni olib yurardi:

| # | Joy | Xato ko'rinishi |
|---|---|---|
| 1 | `inventory._number` | `OverflowError` |
| 2 | `oee._number` | `OverflowError` |
| 3 | `manufacturing._number` | `OverflowError` |
| 4 | `workforce._hours` | `OverflowError` |
| 5 | `business_graph.canonical` | `OverflowError` **va** jimgina `inf` |

### §131.2. Nuqson 1 — `float()` **ko'taradi**, qaytarmaydi

To'rtta hujayra o'quvchining docstring'i bir xil va'da beradi: "finite bo'lmagan
hujayra **o'qilmaydigan** bo'ladi — sanaladi, hech qachon targ'ib qilinmaydi".
To'rttasi ham `inf` va `nan` ni allaqachon rad etadi.

Lekin `float()` float oralig'idan tashqari **butun son** uchun cheksizlik
**qaytarmaydi — `OverflowError` ko'taradi**. O'lchandi:

```
inventory._number(10**400)      -> RAISED OverflowError
oee._number(10**400)            -> RAISED OverflowError
manufacturing._number(10**400)  -> RAISED OverflowError
workforce._hours(10**400)       -> RAISED OverflowError
```

Ya'ni JSON provayder qaytargan 400 xonali son **o'qishni yiqitardi**, "o'qilmadi"
deb sanalish o'rniga.

### §131.3. Nuqson 2 — `canonical` **ziddiyatni o'chirardi**

`business_graph.canonical` — ikki manba **mos keladimi** degan qarorni qabul
qiladi, ya'ni **ziddiyatni topish uning butun vazifasi**.

Satr yo'li **ko'tarmadi — jimgina `inf` qaytardi**:

```
canonical('1' + '0'*400) -> ('n', inf)
canonical('2' + '0'*400) -> ('n', inf)
equal? True
```

Ikki **har xil** ulkan narx **bir xil** deb topildi. Grafik ularni "kelishilgan"
deb xabar qiladi — **haqiqiy ziddiyat jimgina yo'qoladi**. Butun son yo'li esa
`OverflowError` ko'tarardi: bir xil qiymat qanday kelganiga qarab halokatli yoki
jim edi.

Tuzatildi: ifodalab bo'lmaydigan qiymat **matn shakliga** tushadi — deterministik
va butun son yo'li bilan satr yo'li **bir xil** natija beradi.

### §131.4. Nuqson 3 — hech narsani chegaralamaydigan shift

`inventory.py` da `MAX_QUANTITY = 10 ** 15` bor edi. Uni **hech kim o'qimasdi**
(butun repo bo'ylab grep: faqat ta'rif). Va u **aynan shuni aytuvchi izohning
tagida** turardi:

> "A constant that bounds nothing is worse than an absent one, because it
> advertises a limit nobody enforces."

Izoh o'sha shakl `source_priority` dan olib tashlanganini aytadi — keyin **keyingi
qatorda** o'sha shaklni qayta yaratadi. Olib tashlandi.

### §131.5. Topilma 4 — **sinov o'zi o'lchamagan narsani nomlaydi**

Uchta mavjud sinov aynan shu xossani nomlaydi:
`test_a_non_finite_cell_is_unreadable_not_a_huge_number`,
`test_a_nan_cell_is_unreadable_not_a_zero`,
`test_a_negative_infinity_cell_is_unreadable`.

Uchtasi ham **satr** `'inf'` / `'nan'` ni beradi — va `NUMBER_RE` ularni
**finite guard'ga yetmasdan** rad etadi:

```
NUMBER_RE.match('inf') -> None
_number('inf')         -> None   <- regex qildi, guard ishga tushmadi
```

O'lchandi: **finite guard olib tashlanganda uchtasi ham YASHIL qoladi**, faqat
yangi sinov qizil bo'ladi. Ya'ni ular **regexni** o'lchagan, **guard'ni** emas;
guard shu fazagacha **umuman o'lchanmagan** edi.

### §131.6. Revert matritsasi — 11 rejim, hammasi qizil

| Buzilish | Natija |
|---|---|
| N1 `inventory._number`: overflow guard off | QIZIL (`errors=1`) |
| N2 `inventory._number`: finite guard off | QIZIL (`failures=7`) |
| N3 `oee._number`: overflow guard off | QIZIL (`errors=1`) |
| N4 `oee._number`: finite guard off | QIZIL (`failures=1`) |
| N5 `manufacturing._number`: overflow guard off | QIZIL |
| N6 `manufacturing._number`: finite guard off | QIZIL |
| N7 `workforce._hours`: overflow guard off | QIZIL |
| N8 `workforce._hours`: finite guard off | QIZIL |
| N9 `canonical`: satr yo'li yana `inf` ga qulaydi | QIZIL |
| N10 `canonical`: butun son yo'li yana ko'taradi | QIZIL |
| N11 `inventory`: `MAX_QUANTITY` qayta e'lon qilindi | QIZIL |
| **hech narsa** | **YASHIL** |

### §131.7. Qo'shilgan sinovlar (6 ta)

- `test_inventory.py` (82 → 84): `..._quantity_past_the_float_range_...`,
  `test_the_module_declares_no_ceiling_it_does_not_enforce`
- `test_oee.py` (73 → 74), `test_manufacturing.py` (70 → 71),
  `test_workforce.py` (36 → 37): `..._integer_past_the_float_range_...`
- `test_business_graph.py` (174 → 175): `test_two_enormous_values_do_not_collapse_into_one`

Probe: **374 → 409** xossa.

### §131.8. Saboqlar

1. **Shakl bo'yicha supur, modul bo'yicha emas.** Bitta qator beshta modulda
   yashardi; ularning uchtasi bir-birini "allaqachon tuzatilgan" deb havola
   qilardi.
2. **`float()` ikki xil yolg'on gapiradi.** Oraliqdan tashqari butun son uchun
   **ko'taradi**; oraliqdan tashqari **satr** uchun **`inf` qaytaradi**. Ikkinchisi
   xavfliroq: u jim.
3. **Ziddiyatni topadigan funksiyada "jim" — "noto'g'ri" demak.** Ikki har xil
   qiymatning bir xil bo'lib qolishi — yo'qolgan ziddiyat.
4. **O'qish yo'lida hech narsa ko'tarilmasligi kerak.** "O'qilmadi" — javob;
   istisno — emas.
5. **Sinov nomi o'lchov emas.** Uchta sinov "finite" deb nomlanib, aslida regexni
   o'lchagan edi.
6. **Guard'ni **almashtirib** ko'r, shaklni emas, **qiymat turini** ham.**
   Satr va son — bir xil xossaning ikki yo'li.

---

## §132. Fazza yigirma yettinchi — managed database kontrakti chegaralari

### §132.1. Sirt — `database/` qatlami

`platform_runtime/database/` — **15 fayl, 1 897 satr**, o'nta backend va bitta
umumiy kontrakt. Chegara nuqtalari `contract.py` (250 satr) da:

| # | Chegara | Shakl |
|---|---|---|
| 1 | `NAME` identifikatori | `{0,62}` → 1..63 |
| 2 | `MAX_INTEGER = 2**53 - 1` | portativlik shifti |
| 3 | `text(value, maximum=128)` | `1 <= len <= max` |
| 4 | `names` | `1..40` |
| 5 | `scalar` butun son | `-MAX <= v <= MAX` |
| 6 | `scalar` matn | UTF-8 bayt `<= 4000` |
| 7 | `record_key` matn | `1..256` |
| 8 | `parse_request` | UTF-8 bayt `1..12000` |
| 9 | `normalize` `limit` | `1..100` |
| 10 | `normalize` `values` | `1..40` |
| 11 | `normalize` `expected_version` | `1..MAX_INTEGER` |
| 12 | yakuniy so'rov hajmi | `<= 12000` bayt |

### §132.2. Nuqson 1 — **bir fakt, ikki o'quvchi**: versiya shifti

`MAX_INTEGER = 2**53 - 1` — bu kontrakt "portativ" deb ataydigan **eng katta**
butun son, va `scalar` uni **qabul qiladi**. Lekin `normalize` ning
`expected_version` tekshiruvi **qat'iy `<`** ishlatardi:

| Joy | Predikat | Sezgi |
|---|---|---|
| `contract.normalize` (208) | `not 1 <= version < MAX_INTEGER` | **qat'iy** |
| `contract.scalar` (64) | `-MAX_INTEGER <= value <= MAX_INTEGER` | inklyuziv |
| `cassandra_backend.py:138` | `not 1 <= version <= MAX_INTEGER` | inklyuziv |
| `dynamodb_backend.py:129` | `not 1 <= version <= MAX_INTEGER` | inklyuziv |
| `elasticsearch_backend.py:64` | `not 1<=v<=MAX_INTEGER` | inklyuziv |
| `neo4j_backend.py:77` | `not 1<=version<=MAX_INTEGER` | inklyuziv |

O'lchandi: `expected_version = 9007199254740991` da **kontrakt rad etadi,
backendlar qabul qiladi**. Ya'ni bitta fayl **o'zi bilan ham**, to'rt backend bilan
ham ziddiyatda edi.

Va rad etish xabari **yolg'on** edi: `"Update requires positive expected_version"` —
versiya **musbat**, u shunchaki shiftning o'zida.

Tuzatildi: `<=`, va xabar haqiqiy chegarani aytadi.

### §132.3. Nuqson 2 — **bir qiymat, ikki javob**: yolg'iz surrogat

`json.loads` JSON `udXXX` escape'idan **yolg'iz surrogat** yasaydi, ya'ni so'rov
tanasi `str` bo'lib, **kodlanmaydigan** qiymat olib kelishi mumkin. Uchta darvoza
qiymatni UTF-8 hajmi bilan o'lchaydi, va **o'lchovning o'zi** `UnicodeEncodeError`
ko'tarardi — kontrakti `ValueError`/`Forbidden` bo'lgan **validatsiya yo'lida**.

O'lchandi (tuzatishdan oldin):

| Darvoza | `'\ud800'` |
|---|---|
| `text` | **QABUL** (u encode qilmaydi) |
| `scalar` | `UnicodeEncodeError` |
| `record_key` | `UnicodeEncodeError` |

Bir xil qiymat, ikki xil javob, biri **halokat**. Tuzatildi: `_utf8_size` yordamchisi
o'lchovni bitta joyga yig'adi va `ValueError` bilan **nomlab** rad etadi.

### §132.4. **O'zim qilgan xato** — test ko'r edi, revert uni ochdi

Birinchi yozgan sinovim `assertRaises(ValueError)` ishlatardi. **`UnicodeEncodeError`
— `ValueError` ning avlodi** (`UnicodeEncodeError` → `UnicodeError` → `ValueError`),
shuning uchun sinov **aynan o'zi istisno qilmoqchi bo'lgan halokat bilan qanoatlanardi**.

Revert matritsasi ochdi: **P2, P4, P5 yashil qoldi** — ya'ni `scalar`, `parse_request`
va `_utf8_size` ning tuzatishlari **qadalmagan** edi. Tuzatildi:
`assertNotIsInstance(caught.exception, UnicodeError)` + xabar matni tekshiriladi.
Shundan keyin beshtasi ham **QIZIL**.

Bu — auditning takrorlanuvchi mavzusi: **yashil sinov o'lchangan sinov emas.**

### §132.5. Revert matritsasi — 5 rejim

| Buzilish | Test suite |
|---|---|
| P1 versiya shifti `<=` → `<` | **QIZIL** (`failures=1, errors=1`) |
| P2 `scalar` o'lchov → halokat | **QIZIL** (`failures=3`) |
| P3 `text` kodlanuvchanlik darvozasi olib tashlandi | **QIZIL** (`failures=1`) |
| P4 `parse_request` o'lchov → halokat | **QIZIL** (`failures=1`) |
| P5 `_utf8_size` try/except olib tashlandi | **QIZIL** (`failures=4`) |
| **hech narsa** | **YASHIL** (52/52) |

### §132.6. Qo'shilgan sinovlar (48 → 52)

- `test_the_version_ceiling_itself_is_accepted_and_one_past_it_is_not`
- `test_every_version_guard_uses_the_same_ceiling`
- `test_a_lone_surrogate_is_refused_by_name_not_by_crash`
- `test_ordinary_text_is_still_carried`

Probe: **409 → 438** xossa.

### §132.7. Saboqlar

1. **Bir fayl o'zi bilan ziddiyatda bo'lishi mumkin.** `contract.scalar` `<=` der,
   `contract.normalize` `<` der — 150 qator orasida.
2. **Xabar yolg'on bo'lishi mumkin.** "positive" — musbat bo'lmagan qiymat uchun
   emas, **shiftdagi** qiymat uchun ham chiqadi.
3. **`assertRaises(ValueError)` — `UnicodeEncodeError` ni ham qabul qiladi.**
   Istisno **avlodini** tekshir: `assertNotIsInstance(..., UnicodeError)`.
4. **O'lchov joyi — rad etish joyi.** Qiymatni bayt bilan chegaralash
   kodlanuvchanlikni ham tekshiradi; buni boshqa joyga surish — xatoni boshqa
   joyga surish.
5. **Revert matritsasi o'z sinovingni ham tekshiradi.** Bu fazada u mening
   sinovimning ko'rligini ochdi — uchinchi marta.

---

## §133. Fazza yigirma sakkizinchi — agent loop chegaralari va navbat shifti

### §133.1. Sirt — o'n yettita chegara

`platform_runtime/agent_loop.py` (345 satr) — saqlangan, natija bilan
oziqlanadigan rejalashtirish sikli. Chegaralar:

| # | Chegara | Shakl |
|---|---|---|
| 1 | `MAX_STEPS = 12` | `1..12` |
| 2 | `MAX_SECONDS = 86400` | `60..86400` |
| 3 | `MAX_OBSERVATION_BYTES = 12000` | `>` rad |
| 4 | `MAX_HISTORY_BYTES = 48000` | `>` rad |
| 5 | `PLANNER_LEASE_SECONDS = 60` | ijarа muddati |
| 6 | run matni | `1..4000` |
| 7-10 | identifikatorlar | tenant 64, key 256, agent 128, actor 128 |
| 11 | navbat shifti | `pending >= 100` |
| 12 | `answer` | `<= 8000` |
| 13 | `question` | `<= 1000` |
| 14 | `evidence` | `1..MAX_STEPS` |
| 15 | qaror obyekti | `<= 20000` bayt |
| 16 | `_observations` | `len(rows) == steps` |
| 17 | `calls` / `max_calls` | `max_steps + 1` |

### §133.2. Revert matritsasi — **17 tadan 12 tasi yashil**

| Buzilish | Natija |
|---|---|
| R1 `MAX_STEPS` `12` → `6` (**toraytirilgan**) | **YASHIL** |
| R2 `MAX_STEPS` `12` → `13` | QIZIL |
| R3 `MAX_SECONDS` `86400` → `43200` (**toraytirilgan**) | **YASHIL** |
| R4 `MAX_SECONDS` `86400` → `86401` | QIZIL |
| R5 matn shifti `4000` → `4001` | **YASHIL** |
| R6 tenant `64` → `65` | **YASHIL** |
| R7 key `256` → `257` | **YASHIL** |
| R8 navbat shifti `100` → `101` | **YASHIL** |
| R9 `answer` `8000` → `8001` | **YASHIL** |
| R10 `question` `1000` → `1001` | **YASHIL** |
| R11 `evidence` → `MAX_STEPS + 1` | **YASHIL** |
| R12 kuzatuv baytlari `12000` → `12001` | **YASHIL** |
| R13 tarix baytlari `48000` → `48001` | **YASHIL** |
| R14 ijara `60` → `61` | **YASHIL** |
| R15 `ACTIVE` konstantasi | QIZIL |
| R16 `create()` SQL nusxasi | **YASHIL** ← nuqson |

**R1 va R3 — eng qimmatlisi.** Mavjud budjet sinovi `0` va `13` ni **rad** sifatida
yuradi, ya'ni shiftni **bir tomondan** qadaydi: `6` shifti ham `13` ni rad etadi.
Shuning uchun **toraytirilgan** budjet ko'rinmasdi — o'n ikki qadamli run jimgina
oltitada rad etilardi.

### §133.3. Nuqson — **bir fakt, uchta o'quvchi**, va bittasi qarovsiz

Faol holat to'plami **uch joyda** yozilgan:

| Joy | Shakl |
|---|---|
| `ACTIVE` (13) | konstanta |
| `create()` (55) | SQL ichida qo'lda |
| `tick()` (328) | SQL ichida qo'lda |

`_reserve` konstantani ishlatadi. O'lchandi: `create()` nusxasidan `waiting_task`
ni olib tashlash **butun 30 sinovli to'plamni yashil qoldirdi**; xuddi shu o'zgarish
`tick()` da ushlandi.

**Oqibat kosmetik emas:** navbat shifti `waiting_task` runlarini sanashni
to'xtatardi, ya'ni tenant o'sha holatda **cheksiz** run to'plashi mumkin edi — va
har bir qo'riqchi "sog'lom" deb xabar berardi.

**Tuzatish — qayta yozishni olib tashlash:** ikkala SQL ham endi
`_ACTIVE_PLACEHOLDERS` ni `ACTIVE` dan quradi, xuddi `erp.posted` ning
`CLAIMING_STATUSES` dan qurgani kabi.

### §133.4. Revert matritsasi — tuzatishdan keyin

**16 rejim, hammasi QIZIL**, qaytarilgan holat YASHIL. R16 alohida e'tibor talab
qildi: birinchi urinishda mutatsiya **SQL binding xatosi** berdi (3 placeholder,
4 parametr) — ya'ni u **noto'g'ri sababdan** qizil edi. Arity to'g'rilangach
mutatsiya haqiqiy semantik siljishga aylandi va **yangi yozgan navbat sinovi**
qizil bo'ldi.

### §133.5. Qo'shilgan sinovlar (30 → 39)

- `test_the_step_budget_edges_are_accepted_not_merely_bounded`
- `test_the_time_budget_edges_are_accepted_not_merely_bounded`
- `test_the_run_input_ceiling_is_four_thousand_characters`
- `test_the_run_identity_limits_are_the_documented_four`
- `test_the_active_run_queue_ceiling_is_one_hundred`
- `test_the_queue_ceiling_counts_a_waiting_run`
- `test_the_decision_ceilings_are_the_documented_values`
- `test_the_context_byte_ceilings_are_the_documented_values`
- `test_the_active_status_set_is_stated_once`

Probe: **438 → 475** xossa.

### §133.6. **O'zim qilgan uchta xato**

1. **`self.create('q-1', key='q-1')`** — `create` ning birinchi pozitsion
   argumenti **`key`** ning o'zi, shuning uchun `key` ikki marta berilgan
   (`TypeError`). Uchta sinov shu sababdan yiqildi.
2. **`source.count('ACTIVE') == 3`** — `_ACTIVE_PLACEHOLDERS` ichida `ACTIVE` bor,
   shuning uchun son **9** chiqdi. Aniq o'lchov: `_ACTIVE_PLACEHOLDERS` ni sanash.
3. **`if __name__` bloki ostiga qo'shilgan sinov yig'ilmaydi** — bu **uchinchi**
   marta. Fayl parse bo'ldi, discovery `OK` dedi, 9 sinov **hech qachon
   yig'ilmadi**. Endi har qanday qo'shish skripti **avval** `__main__` qo'riqchisini
   qidiradi.
4. **`tick` eng eski nomzodni tanlaydi** — 98 run navbatda turganda `tick` mening
   runimni emas, eng eskisini oldi, shuning uchun run `pending` bo'lib qoldi.
   Sinov qayta qurildi: kutayotgan run **yolg'iz** bo'lganda tick qilinadi.

### §133.7. Saboqlar

1. **Shiftni bir tomondan qadash — yarim qadash.** `13` rad etilishi shift `6`
   bo'lsa ham o'tadi. **Qabul qilinadigan tomon ham** yozilishi shart.
2. **Bir fakt uch joyda — uchtadan biri qarovsiz qoladi.** Bu yerda aynan
   navbatni qo'riqlaydigan nusxa qarovsiz edi.
3. **Mutatsiya noto'g'ri sababdan qizil bo'lishi mumkin.** SQL arity xatosi
   qizil berdi, lekin xossani o'lchamadi. Arity to'g'rilangach haqiqiy o'lchov
   bo'ldi.
4. **Yordamchi funksiya chaqiruvining birinchi pozitsion argumenti — kalit.**
   `create(key, ...)` ni `create(key, key=...)` deb chaqirish mumkin emas.

---

## §134. Fazza yigirma to'qqizinchi — route sirti va UI qamrovi

### §134.1. Sirt

**93 route** o'n bitta modulda. UI — `apps/ui` — 12 tab va 5 komponent.
Savol: **qaysi route'ga UI yetib boradi va qaysi biriga yo'q?**

### §134.2. Usul — **o'lchov**, taassurot emas

Uch marta noto'g'ri o'lchov qurollari yozildi, va **xato rejimining o'zi** saboq:

1. **`startswith`** bilan solishtirish **hamma** route'ni "ishlatilgan" dedi —
   hammaga "ha" deydigan matcher **hech narsani o'lchamaydi**.
2. **Qo'shtirnoq ichidagi satrlarni yig'ish** route segmentini yo'qotdi: komponentlar
   `base + suffix` quradi, `base` esa **backtick shablon** bo'lib ichida
   `encodeURIComponent(...)` bor — qavssiz belgilar sinfi uni
   `/platform/${encodeURIComponent` da **kesib qo'yadi**.
3. **Qo'shtirnoqlarni juftlash** izohdagi apostrofdan keyin **siljiydi**, va
   `identity` **ham mount prefiksi, ham route segmenti** — mount'ni olib tashlash
   route'ni o'chirib tashladi.

Shuning uchun bu bo'lim **xulosa chiqarmaydi**: har bir oila **nomma-nom** sanaladi
va grep bilan UI unga **hech qachon murojaat qilmasligi** tekshiriladi — bu
qayta yurgiziladigan va **rad etilishi mumkin** bo'lgan da'vo.

### §134.3. Nuqson — UI **o'zi taklif qilmagan** chorani buyuradi

`page.tsx:113`, `uncertain` natija uchun:

> "Natija noaniq. Avtomatik qayta ijro bloklangan. Owner tashqi tizimni tekshirib
> **reconcile API orqali** dalil bilan yakunlaydi."

- Route **bor**: `POST /platform/{tenant}/steps/{step}/reconcile`
- UI da **tugma yo'q**: `page.tsx` da `reconcile` so'zi **faqat shu matnda** uchraydi,
  chaqiruv sifatida **hech qayerda** yo'q.

**Nega bu jiddiy:** `uncertain` — platform **ataylab** avtomatik qayta urinishni rad
etadigan yagona holat (chunki ikkinchi urinish hujjatni **ikki marta** joylashi
mumkin). Ya'ni operatorning **yagona** yo'li hujjatlashtirilgan, lekin **qurilmagan**.
Tashqi yozuv noaniq bo'lib qolsa, mahsulot uni yakunlash yo'lini **taklif qilmaydi**.

Bu — §130 dagi `_date_text` va §129 dagi `ORDER BY id` bilan **bir xil shakl**:
matn kodga zid, va foydalanuvchi matnga qarab harakat qiladi.

### §134.4. **20 ta** tenant route'i — backend to'liq, UI **yo'q**

| Oila | Soni | Route'lar |
|---|---:|---|
| `reengagement` | 4 | `GET /reengagement`, `PUT /{policy}`, `GET /{policy}/ledger`, `POST /{policy}/sync` |
| `briefing` | 3 | `GET /briefing`, `PUT /{schedule}`, `GET /{schedule}/ledger` |
| `escalation` | 4 | `GET /escalation`, `PUT /{schedule}`, `GET /{schedule}/ledger`, `POST /{schedule}/disable` |
| `supervisor` | 4 | `GET /supervisor`, `PUT /{section}`, `POST /route`, `GET /history` |
| boshqa | 5 | `POST /schedules`; `POST /customers/{id}/contacts`; `POST /customers/{id}/channel-identities`; `POST /customers/{id}/orders`; `POST /knowledge/{c}/documents/{d}/delete` |

Ya'ni **§128 va §129 da auditdan o'tgan to'rt modul** (escalation, reengagement,
oversight/supervisor) va briefing — **to'liq backend, nol UI**. Bu
`QOLGAN-ISHLAR-INVENTAR-UZ.md` ning "mahsulotlashtirish qoldi" xulosasini
**route darajasida** tasdiqlaydi.

### §134.5. Admin yuzasi — **7 ta** identity route, UI **yo'q**

| Route | Nima |
|---|---|
| `POST /identity/bootstrap` | birinchi owner |
| `POST /identity/logout-all` | barcha sessiyalarni yopish |
| `GET /identity/sessions` | sessiya ro'yxati |
| `POST /identity/workspaces/{id}/select` | workspace almashtirish |
| `POST /identity/workspaces/{id}/invitations` | **odam taklif qilish** |
| `POST /identity/invitations/accept` | taklifni qabul qilish |
| `POST /identity/workspaces/{id}/members/{user_id}/revoke` | **a'zoni olib tashlash** |

Oxirgi uchtasi — `QOLGAN-ISHLAR-INVENTAR-UZ.md` "foydalanuvchi boshqaruvi (CRUD)"
deb belgilagan bo'shliq. Endi u **route nomi bilan o'lchandi**.

### §134.6. Qolgani (ataylab UI'siz)

`GET /approvals/pending` (`approvals.py`), `webhooks/instagram`, `webhooks/telegram`,
`/voice/transcribe`, `/voice/speak`, `runner_ws` ning 5 route'i — bular
**server-to-server** yoki **qurilma** yuzalari, brauzer uchun emas. Bu **nuqson
emas**.

### §134.7. MANIFEST — o'lchangan holat

| Ko'rsatkich | Qiymat |
|---|---|
| `status` | **FAIL** |
| `checked` | 445 |
| `Hash mismatch` | 38 |
| `Unlisted file` | 165 |
| Repo ichida generator | **yo'q** |

Shu sessiyada tegilgan 20 ta fayldan **4 tasi hash**, **16 tasi "unlisted"** —
ya'ni `erp.py`, `inventory.py`, `oee.py`, `manufacturing.py`, `workforce.py`,
`business_graph.py` manifestda **umuman yo'q edi**, o'zgarishdan **oldin ham**.
MANIFEST ZIP tayyorlashda tashqarida yasaladi, shuning uchun development daraxtida
**hech qachon yashil bo'lmaydi**. Bu **audit tuzatishi emas** — reliz asbobsozligi
qarori, shuning uchun **tegilmadi**.

### §134.8. Nima **qilinmadi** va nega

Yuqoridagi bo'shliqlar **UI ishi**, audit tuzatishi emas. Bu muhitda React
typecheck/build **NOT_RUN** (`LOCAL-VERIFICATION-UZ.md`: `bun` yo'q), ya'ni
yozilgan UI **tekshirilmagan** bo'lardi — auditning o'z qoidasi esa
"tekshirilmagan ish topshirilmaydi". Shuning uchun **yozilmadi**, **o'lchandi**.

Sinov ham **qo'shilmadi**, va sabab o'lchandi: route↔UI sinovi satr solishtirishga
tayanadi, uchta urinishning uchtasi **noto'g'ri** javob berdi (yuqorida). Noto'g'ri
musbat ishlab chiqaradigan sinov — sinov emas. Buning o'rniga **probe** yozildi:
u har bir oilani nomma-nom tekshiradi va **rad etilishi mumkin**.

### §134.9. Saboqlar

1. **"UI yo'q" — taassurot; "bu route'ga hech qanday chaqiruv yo'q" — o'lchov.**
   Farqni faqat matcherning **o'zi** ko'rsatdi.
2. **Hammaga "ha" deydigan matcher hech narsani o'lchamaydi.** Birinchi urinish
   "0 yetib borilmagan" dedi.
3. **O'z o'lchov quroling ham tekshirilishi kerak.** Uchtasi noto'g'ri edi;
   uchalasining xatosi **boshqa** edi.
4. **Hujjat kodga zid bo'lsa, foydalanuvchi hujjatga qarab yuradi.** Bu uchinchi
   marta shu shakl (§129, §130, §134).
5. **Tekshirib bo'lmaydigan ishni qilmaslik — qaror, dangasalik emas.**

---

## §135. Fazza o'ttizinchi — xarajat budjeti va tool sxema chegaralari

### §135.1. Sirt — `usage_budget.py` (227 satr) va `tools.py` (280 satr)

| # | Chegara | Joy | Shakl |
|---|---|---|---|
| 1 | `MAX_AMOUNT = 10**15` | `amount` | `0/1 .. MAX` |
| 2 | `bounded(value, 256)` | `bounded` | `1..256` |
| 3 | `max_inflight` | `configure` | `1..100` |
| 4 | valyuta | `configure` | `[A-Z]{3}` |
| 5 | `warning_80_percent` | `summary` | `(spent+reserved)*5 >= limit*4` |
| 6 | `limit_exceeded` | `summary` | `spent+reserved > limit` |
| 7 | `available_micro` | `summary` | `max(0, ...)` |
| 8 | navbat `limit` | `pending` | `1..100` |
| 9 | `count >= max_inflight` | `reserve` | eksklyuziv |
| 10 | hisob `UPDATE ... <=` | `reserve` | inklyuziv |
| 11 | token chegarasi | `token_cost` | `0..10**8` |
| 12 | yaxlitlash | `token_cost` | `+999999 // 10**6` |
| 13 | `evidence` | `reconcile` | `1..500` |
| 14 | argument obyekti | `validate_schema` | `<= 20000` |
| 15 | `integer` default | `validate_schema` | `±10**12` |
| 16 | `array` default | `validate_schema` | `maxItems 100` |
| 17 | `string` default | `validate_schema` | `maxLength 4000` |

### §135.2. Nuqson 1 — **ikki matn darvozasi kelishmagan**

| Qoida | `usage_budget.bounded` | `database.contract.text` |
|---|---|---|
| boshqaruv belgisi `< 32` | rad | rad |
| **DEL `0x7F`** | **qabul** | **rad** |
| **bosh/oxirgi bo'shliq** | **qabul** | **rad** |

Ikkalasi ham **bir faktni** — "chegaralangan identifikator" — qo'riqlaydi:
tenant id, actor, reconcile evidence. O'lchandi: `bounded('a\x7fb')` va
`bounded(' a')` **o'tadi**, `contract.text` esa ikkalasini ham **rad etadi**.

Ya'ni bir xil qiymat **qaysi eshikdan kirganiga qarab** haqiqiy yoki noloyiq.
Tuzatildi: `bounded` endi aynan o'sha ikki qoidani qo'llaydi.

### §135.3. Nuqson 2 — **bitta literal, ikki birlik**

`tools.validate_schema` argument obyektini `len(encode(value))` bilan chegaralardi
— ya'ni **belgilar** bilan. Bu runtime'dagi **qolgan hamma** serializatsiya
chegarasi **bayt** o'lchaydi:

| Joy | Birlik |
|---|---|
| `contract.parse_request` | 12000 **bayt** |
| `erp._observations` | 12000 / 48000 **bayt** |
| `dynamodb_backend` | 1024 **bayt** |
| `elasticsearch_backend`, `redis_backend` | 16000 **bayt** |
| **`tools.validate_schema`** | **belgi** ← yagona istisno |

O'lchandi: **20 000 belgidan kam, lekin 20 000 baytdan ko'p** kirillcha obyekt
**qabul qilinardi** — ya'ni e'lon qilingan hajmdan **taxminan ikki barobar**.
Tuzatildi: bayt bilan o'lchanadi.

### §135.4. **Qoldirilgan** nomuvofiqlik — `maxLength` belgi, `parse_request` bayt

`string(maxLength)` **belgi** sanaydi (JSON Schema'da `maxLength` — belgi), parser
esa **bayt** sanaydi (bayt oqimida boshqa ma'nosi yo'q). Natijada:

- `request_json` 12000 **belgidan** kam, **20000 baytdan** kam, lekin **12000
  baytdan ko'p** bo'lsa → **sxema qabul qiladi, kontrakt rad etadi**.
- Xuddi shu so'rov **ASCII** da ikkalasidan ham o'tadi.

Ya'ni farq **kodlashda**, mantiqda emas. **Tuzatilmadi**, va sabab aniq: ikkala son
ham yakka holda **to'g'ri**, ularni bir xil qilish — **siyosat qarori** (hamma
joyda baytmi yoki belgimi), audit esa bunday qarorni **bir tomonlama qabul
qilmaydi**. Probe'da **o'lchangan holda** qayd etildi.

### §135.5. Revert matritsasi — 19 rejim

| Buzilish | Natija |
|---|---|
| S1 `bounded`: DEL tekshiruvi olib tashlandi | QIZIL |
| S2 `bounded`: `strip` tekshiruvi olib tashlandi | QIZIL |
| S3 `bounded`: `256` → `257` | QIZIL |
| S4 `amount`: shift `MAX_AMOUNT` → `MAX_AMOUNT - 1` | QIZIL |
| S5 `amount`: `zero=False` nolni qabul qiladi | QIZIL |
| S6 `warning_80`: `>=` → `>` | QIZIL |
| S7 `limit_exceeded`: `>` → `>=` | QIZIL |
| S8 `reserve`: `>=` → `>` | QIZIL |
| S9 hisob `UPDATE` `<=` → `<` | QIZIL |
| S10 `token_cost`: shift yaxlitlash → pastga | QIZIL |
| S11 token chegarasi `10**8` → `10**8 + 1` | QIZIL |
| S12 `max_inflight` `100` → `101` | QIZIL |
| S13 `pending` `100` → `101` | QIZIL |
| S14 `evidence` `500` → `501` | QIZIL |
| S15 obyekt chegarasi yana belgi bilan | QIZIL |
| S16 `integer` default `10**12` → `10**13` | QIZIL |
| S17 `array` `100` → `101` | QIZIL |
| S18 `string` `4000` → `4001` | QIZIL |
| S19 boolean yana butun son | QIZIL |
| **hech narsa** | **YASHIL** |

**Birinchi yurgizishda to'rttasi yashil qoldi** (S5, S12, S13, S14) — men yozgan
sinovlar bu to'rt chegara **literalini** qamramagan edi. To'rtta sinov qo'shildi va
shundan keyin **19/19 qizil**.

### §135.6. Qo'shilgan sinovlar (+12)

**`test_usage_budget.py`** (36 → 46):
- `test_a_bounded_identifier_refuses_what_the_contract_refuses`
- `test_the_amount_floor_is_zero_or_one_depending_on_the_caller`
- `test_the_warning_threshold_is_inclusive_at_eighty_percent`
- `test_the_limit_is_inclusive_so_exactly_the_limit_is_not_exceeded`
- `test_the_parallel_ceiling_admits_exactly_max_inflight`
- `test_the_parallel_call_ceiling_is_one_hundred`
- `test_the_pending_limit_ceiling_is_one_hundred`
- `test_the_reconcile_evidence_ceiling_is_five_hundred`
- `test_token_cost_rounds_up_and_bounds_its_inputs`
- `test_a_boolean_is_never_an_amount`

**`test_adapters.py`** (16 → 18):
- `test_the_argument_object_bound_is_measured_in_bytes`
- `test_the_default_schema_bounds_are_the_documented_values`

Probe: **507 → 572** xossa.

### §135.7. **O'zim qilgan xatolar**

1. **Sinovni fayl oxiriga qo'shdim, u boshqa sinfga tushdi.** `test_usage_budget.py`
   da **ikki sinf** bor (`BudgetTests`, `MeteringTests`); EOF — ikkinchisining
   ichi. To'rtta sinov `self.b` va `self.reserve` ni topolmadi.
2. **Keyin uni ko'chirishda blokni ikki marta yozdim** va u `class` dan **oldin**
   tushib, `IndentationError` berdi. To'g'ri yechim: blokni **bir marta** ajratib
   olib, **ikkinchi** `class` qatoridan oldin qo'yish.
3. **Probe'da manifest sonini qotirib qo'ydim** (`38`). Har faza bu sonni
   o'zgartiradi, shuning uchun qotirilgan son **har safar** yiqiladi. Endi **shakl**
   tekshiriladi (ikkala xato turi ham bor) + muhim da'vo (`erp.py` manifestda yo'q).
4. **`pending('a', 100) == []`** — ro'yxat bo'sh emas, chunki budjetda rezervlar
   bor. Xossa — "chaqiruv qabul qilinadi", "ro'yxat bo'sh" emas.

### §135.8. Saboqlar

1. **Ikki darvoza bir faktni qo'riqlasa, qoidalari **aynan** bir xil bo'lishi
   kerak.** `ord(c) < 32` DEL ni ushlamaydi — u C0 boshqaruv belgisi emas.
2. **Serializatsiya chegarasi — bayt.** "Belgi" bilan o'lchash bir xil sonni
   **ikki barobar** kengaytiradi.
3. **Ikkala son ham to'g'ri bo'lishi mumkin, va baribir kelishmasligi mumkin.**
   `maxLength` — belgi, `parse_request` — bayt. Bu **siyosat** qarori, va uni
   audit bir tomonlama qabul qilmasligi kerak.
4. **Yashil sinov — o'lchangan sinov emas, yana.** To'rtta chegara literalini
   faqat **ikkinchi** revert matritsasi ochdi.
5. **Fayl oxiridagi sinf — sinov qabri.** `if __name__` bloki (§130) va **ikkinchi
   sinf** — bir xil tuzoqning ikki ko'rinishi.

---

## §136. Fazza o'ttiz birinchi — WhatsApp oynasi, imzo va e'lon qilingan shiftlar

### §136.1. Sirt

`whatsapp.py` (38 472 bayt) va `whatsapp_inbound.py` (31 629 bayt) — bitta
**24 soatlik xizmat oynasi** atrofida qurilgan ikki blok. O'n beshta chegara:

| # | Chegara | Joy | Qiymat |
|---|---|---|---|
| 1 | `WINDOW_SECONDS` | `whatsapp` | `24*60*60` |
| 2 | `WINDOW_SECONDS` | `whatsapp_inbound` | `24*60*60` |
| 3 | `MAX_BODY_BYTES` | `whatsapp_inbound` | 1 000 000 |
| 4 | imzo prefiksi | `verify_signature` | `sha256=` (registrga sezgir) |
| 5 | hazm uzunligi | `verify_signature` | **aniq 64** |
| 6 | `MAX_ENTRIES` / `MAX_CHANGES` | `whatsapp_inbound` | 50 / 50 |
| 7 | `MAX_MESSAGES_PER_CHANGE` | `whatsapp_inbound` | 50 |
| 8 | `MAX_TEXT_CHARS` | ikkala modul | 4096 |
| 9 | `MAX_STATUS_CHARS` | `whatsapp_inbound` | 32 |
| 10 | `MESSAGE_ID_RE` | `whatsapp_inbound` | 1..128 |
| 11 | `WA_ID_RE` | `whatsapp_inbound` | 7..20 raqam |
| 12 | `PHONE_RE` | `whatsapp` | 7..15 raqam, noldan boshlamaydi |
| 13 | `NAME_RE` / `LANGUAGE_RE` | `whatsapp` | 1..64 / `ll` yoki `ll_CC` |
| 14 | `MAX_EVENTS_SCANNED` | `whatsapp` | 500 |
| 15 | `MAX_RECIPIENTS` / `MAX_TEMPLATES` / `MAX_BODY_PARAMS` / `MAX_PARAM_CHARS` | `whatsapp` | 200 / 100 / 20 / 400 |

### §136.2. Nuqson — **bir fakt, ikki e'lon, va **assimetrik** siljish**

Oyna **ikki modulda** e'lon qilingan. Bu **ataylab**: `whatsapp_inbound.py` ning
o'z izohi aytadi — *"this module has no import edge to it at module scope: the
outbound module is imported lazily, in `declared_contacts`"*. Shuning uchun audit
**e'lonni olib tashlamadi** (birinchi urinishda olib tashladim va **qaytardim** —
u ochiq-oydin dizayn qaroriga zid edi).

Lekin **qayta yozish narxi to'lanmagan edi**: ikki qiymat **tasodifan** teng edi,
va siljish **jim hamda assimetrik** bo'lardi:

| Yo'l | Oynani kim belgilaydi |
|---|---|
| **hodisalar yo'li** | saqlangan `window_until` **inbound** konstantasi bilan yoziladi; outbound o'qiydigan yo'l o'z nusxasini **ayiradi**, `window_state` esa **qo'shadi** — juftlik **qisqaradi**, ya'ni oyna **inbound** qiymatiga ergashadi |
| **registr yo'li** | jadvalda haqiqiy `last_inbound` vaqti bor, shuning uchun `window_state` **outbound** konstantasini qo'shadi |

**O'lchandi:** outbound konstantasi **bir soat** qilinsa, hodisalar yo'li
**yigirma to'rt soatda qoladi**, registr yo'li esa **bir soatga** tushadi. Bitta
tenant, **ikki xil oyna uzunligi**, qaysi manba javob berganiga qarab — va
**hech narsa yiqilmadi**.

Tuzatish: e'lon **qoldirildi**, lekin tenglik endi **majburiy** —
`test_the_two_service_window_constants_are_one_fact` ikkalasi ajralib ketsa
yiqiladi, va ikki o'qish yo'li alohida qadaldi.

### §136.3. Revert matritsasi — **15 tadan 7 tasi yashil**

| Buzilish | Natija |
|---|---|
| W1 inbound oynasi 23 soatga siljidi | QIZIL |
| W2 outbound oynasi 23 soatga siljidi | QIZIL |
| W3 `window_state` muddatda `>` → `>=` | QIZIL |
| W4 `last <= 0` → `last < 0` | QIZIL |
| W5 hodisalar yo'li konvertatsiyani tashladi | QIZIL |
| W6 imzo tana chegarasi `>` → `>=` | QIZIL |
| **W7 `MAX_BODY_BYTES` `1_000_000` → `1_000_001`** | **YASHIL** |
| W8 hazm uzunligi `64` → `63` | QIZIL |
| W9 prefiks registrga sezgirligi olib tashlandi | QIZIL |
| **W10 `MESSAGE_ID_RE` `128` → `129`** | **YASHIL** |
| **W11 `PHONE_RE` `15` → `16`** | **YASHIL** |
| **W12 `MAX_ENTRIES` `50` → `51`** | **YASHIL** |
| **W13 `MAX_TEXT_CHARS` `4096` → `4097`** | **YASHIL** |
| **W14 `MAX_EVENTS_SCANNED` `500` → `501`** | **YASHIL** |
| **W15 `ERROR_OUTSIDE_WINDOW` `131047` → `131048`** | **YASHIL** |

Tuzatishdan keyin **15/15 qizil**, ikkala control yashil.

**W7 alohida e'tibor talab qildi.** Xulq sinovi o'z yukini **o'sha simvoldan**
quradi (`MAX_BODY_BYTES`, `MAX_BODY_BYTES + 1`), shuning uchun shift kengaysa
sinov **u bilan birga suriladi** va yashil qoladi. Bu — **§129 dagi M5 sabog'i,
uchinchi marta**. Endi literal **alohida** qadaldi.

### §136.4. Qo'shilgan sinovlar (+9)

**`test_whatsapp.py`** (120 → 127):
- `test_the_two_service_window_constants_are_one_fact`
- `test_the_two_window_reads_follow_different_constants`
- `test_the_phone_ceiling_is_fifteen_digits`
- `test_the_outbound_text_ceiling_is_four_thousand_and_ninety_six`
- `test_the_event_scan_ceiling_is_five_hundred`
- `test_the_outside_window_error_code_is_the_one_meta_sends`
- `test_the_recipient_and_template_ceilings`

**`test_whatsapp_inbound.py`** (70 → 74):
- `test_the_webhook_body_bound_is_one_megabyte`
- `test_the_entry_ceiling_is_fifty`
- `test_the_change_ceiling_is_fifty`
- `test_the_message_id_ceiling_is_one_hundred_and_twenty_eight`

Probe: **572 → 627** xossa.

### §136.5. **O'zim qilgan xatolar**

1. **Dizayn qaroriga zid tuzatish.** Avval `whatsapp_inbound` dan e'lonni olib
   tashlab, `whatsapp` dan import qildim — modul darajasidagi import chekkasi
   **ataylab yo'q**, va bu izohda yozilgan. Fayl izohini o'qimasdan tuzatish —
   auditning o'zi qoralaydigan xatti-harakat. **Qaytarildi** va o'rniga tenglik
   **majburiy** qilindi.
2. **Harness yana satr oxiriga urildi.** `whatsapp.py` — **CRLF**; naqshlarim LF
   edi, shuning uchun W4 "PATTERN-MISSING" berdi va **yashil ko'rindi**.
3. **W1 ni noto'g'ri modulda yurgizdim** — tenglik sinovi `test_whatsapp.py` da,
   men esa `test_whatsapp_inbound.py` ni yurgizdim.
4. **W7 simvoldan yasalgan sinov bilan "qadaldi" deb o'yladim** — u shift bilan
   birga suriladi. Uchinchi marta shu tuzoq.

### §136.6. Saboqlar

1. **Qayta yozilgan faktning narxi — majburiy tenglik.** Dizayn uni taqiqlamasa,
   **sinov** uni ushlashi kerak.
2. **`-W` keyin `+W` — juftlik qisqaradi.** Ikki yo'l bir xil konstantani
   ishlatadi deb o'ylash oson; o'lchov ular **boshqa** konstantaga ergashishini
   ko'rsatdi.
3. **Xulq sinovi simvoldan yasalsa, shiftni isbotlamaydi.** Uchinchi marta.
4. **Izohni o'qimasdan tuzatish — dizaynni buzish.** Ataylab qilingan qaror
   "xato"ga o'xshab ko'rinadi.
5. **`-mmin`/CRLF kabi muhit tafsilotlari matritsani jimgina yashil qiladi.**

---

## §137. Fazza o'ttiz ikkinchi — telephony chegaralari va **uchinchi** o'lik konstanta

### §137.1. Sirt — `telephony.py` (51 566 bayt)

Qo'ng'iroq hodisalari o'quvchisi va **consent darvozasi**. O'n to'qqizta chegara:

| # | Chegara | Qiymat |
|---|---|---|
| 1 | `MAX_ROWS` / `MAX_EVENTS` | 200 / 200 |
| 2 | `MAX_REGISTERS` | 20 |
| 3 | `MAX_PURPOSES` | 16 |
| 4 | `MAX_QUEUE_ITEMS` | 50 |
| 5 | `MAX_PER_WINDOW` | 1000 |
| 6 | `MIN_WINDOW_SECONDS` / `MAX_WINDOW_SECONDS` | 60 / 86 400 |
| 7 | `MIN_RETENTION_DAYS` / `MAX_RETENTION_DAYS` | 1 / 3650 |
| 8 | `MAX_PURPOSE_CHARS` | 64 |
| 9 | `MIN_NUMBER_DIGITS` / `MAX_NUMBER_DIGITS` | 7 / 15 |
| 10 | `MAX_DURATION` | 86 400 |
| 11 | `NAME_RE` / `DAY_RE` | 1..64 / ISO |
| 12 | `_LOCATOR_RE` | media kengaytmalari |
| 13 | `DIRECTIONS` / `PURPOSES` / `CONSENT_STATUS` | 2 / 5 / 2 |

### §137.2. Nuqson — **uchinchi marta** o'lik konstanta

| Konstant | Holat |
|---|---|
| `telephony.MAX_QUEUE = 200` | izoh bloki uni *"how many callable rows one read may return at all"* deb **hujjatlashtiradi** — lekin o'qishni `MAX_ROWS`, tashiladigan elementlarni `MAX_QUEUE_ITEMS` chegaralaydi. **Izoh nomlagan shift mavjud emas edi.** |
| `telephony.MAX_WINDOW_DAYS = 31` | hech kim o'qimaydi |
| `vision.MAX_WINDOW_DAYS = 31` | **o'sha konstanta ikkinchi modulda qayta yozilgan**, va u yerda ham o'qilmaydi |

`MAX_WINDOW_DAYS` ning ko'rinib turgan maqsadi — `since`..`until` **oralig'i**
uchun shift. `_day` ikkalasining **FORMATINI** tekshiradi, **masofasini emas** —
`2026-13-45` o'tadi, va o'n yillik oyna ham rad etilmaydi. Ya'ni o'sha shift
**hech qachon ulanmagan**.

**Bu shakl uchinchi marta topildi:** Fazza I `inventory.MAX_NAMES` va
`oee.MAX_NAMES` ni, Fazza 26 `inventory.MAX_QUANTITY` ni olib tashlagan edi.

Uchtasi ham **olib tashlandi** va izohlar **haqiqatan qo'llaniladigan** konstantalarni
nomlaydi. **Yetishmayotgan oraliq shifti** esa **BACKLOG.json** ga ochiq risk
sifatida yozildi — **ixtiro qilinmadi**, chunki sonni tanlash **operatorning**
qarori, auditning emas.

### §137.3. Revert matritsasi — **19 tadan 15 tasi yashil**

| Buzilish | Natija |
|---|---|
| T1 `MAX_PER_WINDOW` 1000 → 1001 | **YASHIL** |
| T2 `MIN_WINDOW_SECONDS` 60 → 61 | **YASHIL** |
| T3 `MAX_WINDOW_SECONDS` 86400 → 86401 | **YASHIL** |
| T4 `MIN_RETENTION_DAYS` 1 → 2 | **YASHIL** |
| T5 `MAX_RETENTION_DAYS` 3650 → 3651 | QIZIL |
| T6 `MIN_NUMBER_DIGITS` 7 → 6 | QIZIL |
| T7 `MAX_NUMBER_DIGITS` 15 → 16 | QIZIL |
| T8 `MAX_DURATION` 86400 → 86401 | **YASHIL** |
| T9 `MAX_QUEUE_ITEMS` 50 → 51 | **YASHIL** |
| T10 `MAX_EVENTS` 200 → 201 | **YASHIL** |
| T11 `MAX_ROWS` 200 → 201 | **YASHIL** |
| T12 `MAX_PURPOSES` 16 → 17 | QIZIL |
| T13 `MAX_REGISTERS` 20 → 21 | **YASHIL** |
| T14 `MAX_PURPOSE_CHARS` 64 → 65 | **YASHIL** |
| T15 `NAME_RE` `{0,63}` → `{0,64}` | **YASHIL** |
| T16 lokator qoidasi `.wav` ni yo'qotdi | **YASHIL** |
| T17 `MAX_QUEUE` qayta e'lon qilindi | **YASHIL** |
| T18 `MAX_WINDOW_DAYS` telephony'da qayta e'lon qilindi | **YASHIL** |
| T19 `MAX_WINDOW_DAYS` vision'da qayta e'lon qilindi | **YASHIL** |

Tuzatishdan keyin **19/19 qizil**, ikkala control yashil.

**T16 diqqatga sazovor:** lokator qoidasidan **bitta** kengaytmani (`.wav`) olib
tashlash butun to'plamni yashil qoldirdi — ya'ni **o'n uchta kengaytmaning biri
ham** qadalgan emas edi.

### §137.4. Qo'shilgan sinovlar (+9)

**`test_telephony.py`** (139 → 147):
- `test_the_throughput_pace_edges_are_the_documented_pair`
- `test_the_retention_edges_are_the_documented_pair`
- `test_the_queue_and_scan_limits_are_the_documented_ceilings`
- `test_the_register_purpose_and_text_ceilings`
- `test_the_call_duration_ceiling_is_one_day`
- `test_the_name_ceiling_is_sixty_four_characters`
- `test_a_media_filename_is_not_a_phone_number`
- `test_the_module_declares_no_ceiling_it_does_not_enforce`

**`test_vision.py`** (49 → 50):
- `test_the_module_declares_no_ceiling_it_does_not_enforce`

Probe: **627 → 697** xossa.

### §137.5. **O'zim qilgan xato**

1. **`normalise` `None` qaytaradi deb o'yladim** — u **bo'sh satr** qaytaradi, va
   bu docstring'da aniq yozilgan. O'lchov apparatim xato edi, kod emas.
2. **`maximum=MAX_PURPOSE_CHARS`** deb qidirdim — haqiqiy qator
   `len(item) > MAX_PURPOSE_CHARS`. Manbani **o'qimasdan** naqsh yozdim.

### §137.6. Saboqlar

1. **O'lik konstanta — takrorlanuvchi kasallik.** Uch faza, oltita konstanta. Har
   biri "hech kim o'qimaydigan chegara" edi, va ikkitasi **izohda hujjatlashtirilgan
   qoidani** da'vo qilardi.
2. **Izoh koddan ajralib qoladi.** `MAX_QUEUE` "o'qish shifti" deb
   hujjatlashtirilgan, lekin o'qishni boshqa konstanta chegaralaydi.
3. **Format tekshiruvi masofa tekshiruvi emas.** `2026-13-45` o'tadi; `since` va
   `until` orasidagi masofa hech qayerda tekshirilmaydi.
4. **O'n uchta kengaytmaning biri ham qadalgan emas edi.** Bitta elementni olib
   tashlab ko'rish — butun ro'yxatni tekshirishning eng tez yo'li.
5. **Manbani o'qimasdan naqsh yozma.** Ikki marta xato qildim; ikkalasi ham
   **o'lchov apparatim** edi.

---

## §138. Fazza o'ttiz uchinchi — connector qatlami

### §138.1. Sirt — uch modul, 374 satr

| Modul | Satr | Nima |
|---|---|---|
| `connectors.py` | 245 | operator tomonidan tayyorlangan, tenant doirasidagi o'qish |
| `connector_contract.py` | 103 | versiyalangan kontrakt va fail-closed validatsiya |
| `connector_authority.py` | 26 | fail-closed o'qish vakolati |

Chegaralar: identifikator 63 belgi, ulanish nomi 128, allowlist 40 ustun, so'rov
`limit` 1..100, filtr qiymati 1000, qator 16 000 bayt / jami 80 000, `SQLITE_LIMIT_*`
16 000/100 000, progress 1000 chaqiruv / 2 soniya, `_string` 128, `_strings` 100.

### §138.2. Nuqson — **uchta darvoza, ikki qoida**

`authorize_read_connection` ulanish **nomini** faqat **uzunlik** bilan tekshirardi
(`1 <= len(name) <= 128`), holbuki o'sha turdagi qiymat uchun qolgan **ikki**
darvoza — `connector_contract._string` va `database.contract.text` — **atrofdagi
bo'shliqni ham rad etadi va boshqaruv belgilarini (shu jumladan DEL) ham**.

O'lchandi:

| Qiymat | `authorize_read_connection` | `_string` | `contract.text` |
|---|---|---|---|
| `' erp'` | **o'tadi** | rad | rad |
| `'erp '` | **o'tadi** | rad | rad |
| `'e\x7frp'` (DEL) | **o'tadi** | rad | rad |
| `'e\x01rp'` | **o'tadi** | rad | rad |

Bu — **§135 da topilgan bir xil ajralish** (`usage_budget.bounded` ↔
`contract.text`), va **bir xil tuzatildi**: bitta qoida, ikkita emas.

### §138.3. Revert matritsasi — **22 tadan 14 tasi yashil**

| Guruh | Yashil |
|---|---|
| `connectors.py` | C1–C14 (identifikator, nom, allowlist, ustunlar, `limit`, filtr, ikki bayt shifti, progress, SQL limit) |
| `connector_contract.py` | C11–C14, C20 |
| `connector_authority.py` | — (C15–C19 hammasi **qizil**) |

**Vakolat darvozasi yaxshi qadalgan; qolgan ikki modul deyarli o'lchanmagan.**
Tuzatishdan keyin **22/22 qizil**, ikkala control yashil.

### §138.4. **C4 — erishib bo'lmaydigan chegara**

So'rov tomonidagi `1 <= len(columns) <= 40` **hech qachon ishlamaydi**: `columns`
allowlist ning **qismi** bo'lishi shart, allowlist esa allaqachon 40 bilan
chegaralangan. Ya'ni bu **himoyaviy ortiqchalik**, nuqson emas — va shuning uchun
**xulq bilan qadalmadi**; literal sifatida qadaldi va bu **izohda aytilgan**.

### §138.5. **C8 — chegaraga to'g'ri kelish**

Birinchi urinishda 5 qator × 16 000 = 80 000, 6-qator esa 96 000 berdi — ikkala
tomon ham `> 80_000` va `> 80_001` da **bir xil** o'qiladi, shuning uchun mutatsiya
yashil qoldi. **Faqat jami 80 001** ularni ajratadi. Qatorlar shunga moslab
o'lchandi: `4 × 16 000 + 1 001 + 15 000 = 80 001`, har biri qator shiftidan past.

### §138.6. Qo'shilgan sinovlar (+14)

**`test_connector_authority.py`** (37 → 47):
- `test_the_connection_name_gate_matches_its_siblings`
- `test_the_identifier_ceiling_is_sixty_three_characters`
- `test_the_column_allowlist_ceiling_is_forty`
- `test_the_request_column_ceiling_is_forty`
- `test_the_limit_ceiling_is_one_hundred`
- `test_the_filter_value_ceiling_is_one_thousand_characters`
- `test_the_result_byte_ceilings`
- `test_the_query_limits_are_the_documented_values`
- `test_the_tool_schema_agrees_with_the_runtime_ceilings`
- `test_a_query_cannot_reach_a_table_outside_the_allowlist`

**`test_connector_contract.py`** (4 → 8):
- `test_the_bounded_string_gate_matches_its_siblings`
- `test_the_list_ceiling_is_one_hundred`
- `test_a_read_only_driver_may_not_advertise_a_write_capability`
- `test_the_capability_and_scope_ceilings`

Probe: **697 → 780** xossa.

### §138.7. **O'zim qilgan beshta xato**

1. **Bayt shiftini noto'g'ri hisobladim.** `encode({'blob': ''})` — **11** belgi
   (`{"blob":""}`), men **10** deb o'yladim, shuning uchun "chegaradagi" qator
   allaqachon bir bayt oshib ketgan edi. Endi framing **o'lchanadi**.
2. **Allowlist shiftini jadvalni o'zgartirmasdan sinadim** — SQLite authorizer rad
   etdi va `ValueError` o'rniga `DatabaseError` keldi.
3. **`'c\\x7frm'`** — yozuvchi orqali o'tganda bu **matn** `\x7f`, DEL emas.
   `chr(127)` kerak.
4. **`'name'` allowlist da bor** — filtrni "ruxsat etilmagan ustun" deb sinadim.
5. **Sinov fayl oxiriga ikki marta qo'shildi** — birinchi skript bir faylga qo'shib,
   ikkinchisida yiqildi; qayta yurgizganda **dublikat sinf** paydo bo'ldi.

### §138.8. Saboqlar

1. **Bir turdagi qiymat uchun uchta darvoza bo'lsa, qoida bitta bo'lishi kerak.**
   Bu **ikkinchi** marta shu ajralish topildi (§135 dan keyin).
2. **Bayt chegarasini sinash uchun aniq o'lcham kerak.** "Ancha katta" ikkala
   tomonni bir xil o'qiydi.
3. **Erishib bo'lmaydigan chegara — nuqson emas, lekin u ham qadalishi kerak**, va
   *nega* erishib bo'lmasligi izohda aytilishi kerak.
4. **Yozuvchi orqali o'tgan escape — escape emas.** `\x7f` faylda matn bo'lib
   qoladi; belgini `chr()` bilan yasa.
5. **Sinov faylini ikki marta tahrirlash dublikat yaratadi.** Skript idempotent
   bo'lishi kerak yoki qo'shishdan oldin mavjudligini tekshirishi kerak.

---

## §139. Fazza o'ttiz to'rtinchi — knowledge chegaralari va **o'lishni rad etgan qo'riqchi**

### §139.1. Sirt — `knowledge.py` (257 satr)

Chunking, BM25, ixtiyoriy cosine fusion va ACL bilan boshqariladigan qidiruv.

| # | Chegara | Qiymat |
|---|---|---|
| 1 | identifikator | 1..128 |
| 2 | `MAX_DOCUMENT_CHARS` | 100 000 |
| 3 | `MAX_COLLECTION_CHUNKS` | 4000 |
| 4 | `CHUNK_SIZE` / `OVERLAP` | 512 / 64 |
| 5 | embedding magnitudasi | `abs(n) <= 1e6` |
| 6 | `dimension` | 0..1024 |
| 7 | `model` | `<= 256` |
| 8 | agent nomi | `<= 128` |
| 9 | `title` | `<= 200` |
| 10 | `source_url` | `<= 2000`, HTTP(S), credentialsiz |
| 11 | `expected_version` | `0 <= v < 2**31` |
| 12 | `documents` `limit` | 1..100 |
| 13 | `search` `limit` | 1..5 |
| 14 | `query` | `<= 500` va tokenlarga ajralishi shart |

### §139.2. Nuqson — **qo'riqchining o'zi o'ladi**

`vector()` har bir elementni shunday tekshirardi:

```python
if type(n) not in (int, float) or not math.isfinite(n) or abs(n) > 1e6:
```

`math.isfinite` qiymatni **float'ga aylantiradi**, va `math.isfinite(10 ** 400)`
**`OverflowError` ko'taradi**. Ya'ni float oralig'idan tashqari butun son
`abs(n) > 1e6` ga **hech qachon yetib bormaydi** — holbuki aynan o'sha tekshiruv uni
**to'g'ri rad etardi**.

**Bu §131 dagi shaklning ikkinchi uchrashi** (`inventory._number`,
`oee._number`, `manufacturing._number`, `workforce._hours`,
`business_graph.canonical`), va **o'tkirligi** shunda: u yerda qo'riqchi **yo'q**
edi; bu yerda **qo'riqchining o'zi** yiqiladi — qiymatni chegaralash uchun
qo'yilgan tekshiruv o'ladi.

O'lchandi (tuzatishdan oldin):

| Qiymat | Natija |
|---|---|
| `vector([10**400, 1], 2)` | **`OverflowError`** |
| `vector([10**308, 1], 2)` | `ValueError` (`isfinite` True qaytaradi, chegara ushlaydi) |
| `vector([10**6, 1], 2)` | **o'tadi** (chegara inklyuziv) |
| `vector([10**6 + 1, 1], 2)` | `ValueError` |

**Tuzatish — tartibni almashtirish.** `abs()` ixtiyoriy butun son bilan
**aylantirmasdan** ishlaydi, shuning uchun magnituda tekshiruvi **birinchi** turadi;
finiteness tekshiruvi esa `nan` ni **baribir** ushlaydi (unda har qanday taqqoslash
`False`).

### §139.3. Revert matritsasi — **20 tadan 15 tasi yashil**

Modul **deyarli butunlay o'lchanmagan** edi. Tuzatishdan keyin **20/20 qizil**.

**K7 — eng muhimi:** mening **tuzatishimni qaytarish** (finiteness'ni yana
birinchi qo'yish) **hech qanday sinovni yiqitmasdi**. Ya'ni nuqson **qayta
kiritilishi mumkin edi**. Shu sababli test qo'shildi.

### §139.4. **To'rtinchi marta**: bog'lanmagan `assertIn`

Versiya shifti uchun literalni **qatorga bog'lash** kerak bo'ldi:
`'not 0 <= expected_version < 2**31'` — `'... < 2**31 + 1'` ning **prefiksi**, shuning
uchun bog'lanmagan `assertIn` **aynan o'zi ushlash uchun yozilgan mutatsiya bilan
qanoatlanadi**. Bu tuzoq **to'rtinchi marta** uchradi (§129 M5, §130 M3, §136 W7).

### §139.5. Qo'shilgan sinovlar (34 → 45)

- `test_the_document_ceiling_edges_are_both_measured`
- `test_the_chunk_geometry_is_the_documented_pair`
- `test_the_embedding_magnitude_bound_is_inclusive_at_one_million`
- `test_an_integer_past_the_float_range_is_refused_not_fatal`
- `test_the_identifier_ceiling_is_one_hundred_and_twenty_eight`
- `test_the_collection_configuration_ceilings`
- `test_the_grant_agent_ceiling_is_one_hundred_and_twenty_eight`
- `test_the_document_field_ceilings`
- `test_the_version_ceiling_is_two_to_the_thirty_first`
- `test_the_list_and_search_limits`
- `test_the_collection_chunk_ceiling_bounds_both_reads`

Probe: **780 → 857** xossa.

### §139.6. **O'zim qilgan xatolar**

1. **`math.isfinite(10**400)`** — bu **mening** kashfiyotim emas, lekin uni
   **tasdiqlash** uchun o'lchov kerak bo'ldi: `vector([10**308, 1], 2)` **allaqachon**
   `ValueError` berardi, ya'ni nuqson faqat **float oralig'idan tashqari** butun
   sonda ko'rinadi.
2. **`create_collection(dimension=1024)`** ni **modelsiz** chaqirdim — kod ularni
   birga talab qiladi, ya'ni rad etish **to'g'ri** edi, kutishim noto'g'ri.
3. **`'https://e.uz/'` — 13 belgi**, men 12 dedim; `source_url` sinovlari shiftdan
   pastda qolib, versiya to'qnashuviga yetib bordi. **Ikki marta** xato hisobladim.
4. **`expected_version=0`** ni bir hujjat uchun qayta ishlatdim — birinchi ingest
   uni 1-versiyaga ko'targan edi.
5. **Probe'da chunk shiftini `manuals` da sinadim**, u yerda allaqachon chunklar bor
   edi, shuning uchun `count + len(parts)` chegaraga yetmasdan oshib ketdi.

### §139.7. Saboqlar

1. **Qo'riqchi tartibi ham xossa.** Magnitudani tekshirish **aylantirishdan** oldin
   kelishi kerak; aks holda tekshiruvning o'zi o'ladi.
2. **Ikki xil kattalikdagi "katta son" — ikki xil xato.** `10**308` chegara bilan
   ushlanadi, `10**400` esa **qo'riqchini** yiqitadi. Ikkalasi ham sinalishi kerak.
3. **Tuzatishni qaytarib ko'rmasang, u qadalgan emas.** K7 buni ko'rsatdi.
4. **Bog'lanmagan `assertIn` — to'rtinchi marta.** Prefiks bo'lgan literal
   mutatsiya bilan qanoatlanadi. **Har doim aniq qator.**
5. **Bo'sh bo'lmagan fixture chegarani yashiradi.** `count` nolga teng bo'lmasa,
   jamlanma chegara hech qachon o'lchanmaydi.

---

## §140. Fazza o'ttiz beshinchi — vision chegaralari va **`truncated` yolg'oni**

### §140.1. Sirt — `vision.py` (526 satr)

Zavod kameralari oqimi — **biometrik darvoza** bilan. O'n to'qqizta chegara:

| # | Chegara | Qiymat |
|---|---|---|
| 1 | `MAX_ROWS` / `MAX_EVENTS` | 200 / 200 |
| 2 | `MAX_CLASSES` / `MAX_CLASS_CHARS` | 32 / 64 |
| 3 | `NAME_RE` | 1..64 |
| 4 | `DAY_RE` | ISO sana |
| 5 | registrlar soni | **20** (nomsiz literal) |
| 6 | ustun nomlari | `<= 64` |
| 7 | oyna boshi / oxiri | `>= since` / `<= until` |
| 8 | `_check_window` | `since <= until` |
| 9 | `_scan` limit | `1..MAX_EVENTS` |
| 10 | hodisa to'plash | `len(events) >= limit` |
| 11 | `truncated` | **qaytarilgan narsaga nisbatan** |

### §140.2. Nuqson — `truncated` **hech kim ushlab qolmagan** qatorlarni sanagan

`_scan` bayroqni `matched` dan chiqarardi, u esa **oynadan o'tgan har bir qatorni**
sanaydi — **intern tugun** stansiyali qatorlarni ham, ular aktivga bog'lanmaydi va
shuning uchun **hech qachon hodisa bo'lmaydi**.

O'lchandi (3 bog'lanadigan qator, 7 intern qator, `limit = 5`):

```
scanned=10  count=10  returned=3  unbound=7  truncated=True
```

Ro'yxatda **hamma bog'lanadigan hodisa bor** va limit **hech qachon yetilmagan**, lekin
bayroq operatorga *"kattaroq limit bilan qayta yugur"* dedi — va u **aynan o'sha uch
qatorni** oladi.

**Bu — shu auditning butun probe'i uchun asos bo'lgan xato.** Probe'ning docstring'i
aynan shunday boshlanadi: `truncated` *"is there more, should I ask again"* savoliga
javob beradi, va **noto'g'ri javob** operatorni bir xil ro'yxat qaytaradigan so'rovni
qayta yurishga yuboradi.

`summary` bayroqni `len(ordered) > limit` dan chiqaradi — bu **to'g'ri shakl**, chunki
u aynan `ordered[:limit]` ni qaytaradi. Faqat `_scan` xato edi.

Tuzatish: **qaytarilgan narsaga** nisbatan solishtirish —
`matched - unbound > len(events)`.

### §140.3. Revert matritsasi — **19 tadan 9 tasi yashil**

| Guruh | Natija |
|---|---|
| V14–V19 (biometrik darvoza, `_is_person`, hodisa cap, summary bayrog'i) | **QIZIL** |
| V1, V2 (`MAX_ROWS`, `MAX_EVENTS`) | QIZIL |
| V3–V7 (sinf shiftlari, `NAME_RE`, registr/ustun caplari) | **YASHIL** |
| **V9 (tuzatishni qaytarish)** | **YASHIL** ← eng muhimi |
| V11–V13 (oyna chetlari) | **YASHIL** |
| V18 (summary bayrog'i) | **YASHIL** |

Tuzatishdan keyin **19/19 qizil**.

**V9 alohida:** tuzatishni qaytarish **hech qanday sinovni yiqitmasdi** — nuqson
**qayta kiritilishi mumkin edi**. Bu §139 dagi K7 bilan **bir xil** holat, va shu
sababli endi `truncated` **nimani sanashini** qadaydigan sinov bor.

### §140.4. Qo'shilgan sinovlar (50 → 58)

- `test_the_truncated_flag_counts_events_not_matched_rows`
- `test_the_truncated_flag_is_true_when_the_list_really_was_cut`
- `test_the_class_ceilings`
- `test_the_register_name_and_column_caps`
- `test_the_window_edges_are_inclusive`
- `test_since_must_not_be_after_until`
- `test_the_summary_truncation_flag`
- `test_the_limit_ceiling_is_the_event_ceiling`

Probe: **857 → 914** xossa.

### §140.5. **O'zim qilgan xatolar**

1. **`sensitivity='person'` ni `person_classes` bilan "rad etilishi kerak" deb
   o'yladim** — aslida u **to'g'ri**; rad etilish faqat **sinfsiz** e'lon qilinganda.
2. **`confidence_column=''`** ni xato deb o'yladim — u **ixtiyoriy**, bo'sh bo'lishi
   mumkin.
3. **`nope/sex-1/liniya-1/stanok-1`** ni "bog'lanmaydi" deb o'yladim — `parse_path`
   **nomni emas, chuqurlikni** tekshiradi, shuning uchun u **bog'landi**. Haqiqiy
   bog'lanmaydigan qator — **intern tugun** (`zavod-1/sex-1/liniya-1`, 3 segment).
4. **`sheets_registers` blokini noto'g'ri yozdim** — u `connection`/`spreadsheet_id`/
   `ranges` talab qiladi.
5. **Probe'da uchta qatorni bir stansiyaga qo'ydim** va "summary kesilgan" deb
   kutilgan edim — bir stansiya hech qachon kesilmaydi.
6. **V8 naqshi ikki marta uchradi** (`_scan` va `summary`) — kontekst bilan
   bog'lanishi kerak edi.

### §140.6. Saboqlar

1. **Bayroqning ma'nosi — uning nomi emas, u nimani sanashi.** `truncated`
   *"ko'proq bormi"* degani; uni *"nechta mos keldi"* dan chiqarish — ikki xil savol.
2. **Hisoblagichga qo'shilgan qator, lekin chiqimga qo'shilmagan** — bayroqni
   shishirib yuboradi. Har bir `continue` dan keyin hisoblagichni qayta ko'rib chiq.
3. **Tuzatishni qaytarmasang, u qadalgan emas** — ikkinchi marta (§139 K7, §140 V9).
4. **`parse_path` nomni emas, chuqurlikni tekshiradi.** "Bog'lanmaydigan" fixture
   yasashda bu muhim.
5. **Bir xil stansiya — bitta stansiya.** Kesilishni o'lchash uchun **har xil**
   qiymatlar kerak.

---

## §141. Fazza o'ttiz oltinchi — aktiv ierarxiyasi va **hech narsa ushlanmagan** `truncated`

### §141.1. Sirt — `assets.py` (463 satr)

Aktiv ierarxiyasi — **identifikatsiya umurtqasi**: vision hodisalari, sikl vaqtlari
va OEE roll-up'lari shunga bog'lanadi.

| # | Chegara | Qiymat |
|---|---|---|
| 1 | `SEGMENT_RE` | 1..64 |
| 2 | `MAX_LEVELS` | 8 |
| 3 | `MAX_PATH_CHARS` | 512 |
| 4 | `MAX_CHILDREN` | 200 |
| 5 | `MAX_DESCENDANTS` | 200 |
| 6 | `MAX_MEASUREMENTS` | 100 |
| 7 | `parse_path` chuqurligi | **aniq** `len(levels)` |
| 8 | `prefix_path` chuqurligi | `<= len(levels)` |
| 9 | `is_descendant` | qat'iy chuqurroq |
| 10 | `tree` `depth` | `1..len(levels)` |

### §141.2. Nuqson — `tree` **ushlanmagan sahifani** e'lon qilardi

`tree` o'zi yasagan **har bir** tugunni qaytarardi:

```python
'nodes': [nodes[key] for key in sorted(nodes)],
'count': len(nodes),
'truncated': len(nodes) > MAX_CHILDREN,
```

O'lchandi (`MAX_CHILDREN` 2 ga tushirilib, ierarxiyada 6 tugun):

```
nodes=6  count=6  truncated=True
```

`len(nodes) == count`, ya'ni **hech qanday sahifa ushlanmagan** — butun daraxt
ro'yxatda edi. Va `tree` **`limit` qabul qilmaydi**: u `depth` bilan chegaralanadi,
uni esa **chaqiruvchining o'zi** tanlaydi — ya'ni qayta yurish uchun **kattaroq
qiymat yo'q**. Bayroq **ham yolg'on, ham harakatsiz**.

`MAX_CHILDREN` — **children** tool'ining shifti, bu yerda o'ziniki kabi
ishlatilgan.

`children` va `descendants` **to'g'ri**, va ularning mavjud sinovlari shuni aytadi:
ikkalasi ham `limit` ga kesadi va **populyatsiyani** unga qiyoslaydi. Faqat `tree`
boshqacha edi.

Tuzatish: `'truncated': False`, sababi izohda. `count` hajmni aytadi, `depth` va
`path` esa kamroq so'rashning yo'li.

### §141.3. **Bu shaklning uchinchi uchrashi**

| Faza | Yolg'on bayroq |
|---|---|
| **II** | `len(result) >= limit` — aynan to'lishni "kesilgan" deb o'qigan |
| **35** | `truncated` **hech qachon hodisa bo'lmaydigan** qatorlarni sanagan |
| **36** | `truncated` **hech qanday sahifa ushlamagan** holda kesishni e'lon qilgan |

Uchtasi ham bir xil savolga noto'g'ri javob beradi: *"ko'proq bormi, qayta
so'raymi?"*

### §141.4. Revert matritsasi — **20 tadan 8 tasi yashil**

| Guruh | Natija |
|---|---|
| A7–A10, A13–A17, A19 (chuqurlik qoidalari, kesish bayroqlari, segment bo'yicha moslik) | **QIZIL** |
| A1 (`SEGMENT_RE`), A3 (`MAX_PATH_CHARS`), A4, A5, A6, A12, A18 | **YASHIL** |
| **A11 (tuzatishni qaytarish)** | **YASHIL** ← eng muhimi |
| A20 (path-length tekshiruvi olib tashlandi) | **YASHIL** |

Tuzatishdan keyin **20/20 qizil**.

### §141.5. Qo'shilgan sinovlar (59 → 65)

- `test_the_tree_never_claims_a_cut_it_did_not_make`
- `test_the_segment_ceiling_is_sixty_four`
- `test_the_path_length_ceiling_is_five_hundred_and_twelve`
- `test_the_children_and_descendants_ceilings`
- `test_the_measurement_ceiling_is_one_hundred`
- `test_the_level_and_measurement_name_ceilings`

Probe: **914 → 977** xossa.

### §141.6. **O'zim qilgan xatolar**

1. **Buzuq konstruksiya** yozdim (`with x := (lambda: None):`) — o'lchov skripti
   sintaksis xatosi bilan yiqildi.
2. **Path-length chegarasini `prefix_path` orqali sinadim** — 512 belgili yo'l
   **har doim** 4 tadan ko'p segmentga ega, shuning uchun **chuqurlik** qoidasi
   birinchi ishlaydi va **boshqa** chegarani o'lchaydi. `_segments` kerak edi.
3. **Probe'da eski iborani `not in src` bilan tekshirdim** — olib tashlashni
   qayd etuvchi **izoh** o'sha iborani keltiradi, shuning uchun tekshiruv
   **izoh bilan qanoatlanadi**. Bu Fazza 26 dagi `MAX_QUANTITY` tuzog'i.

### §141.7. Saboqlar

1. **Bayroqning ma'nosi hamma joyda bir xil bo'lishi kerak.** `truncated` —
   "sahifa ushlandi"; uni "ro'yxat katta" ma'nosida ishlatish uni yolg'onchi qiladi.
2. **Boshqa tool'ning konstantasini o'ziniki kabi ishlatma.** `MAX_CHILDREN`
   `children` ning shifti.
3. **Harakatsiz bayroq — yolg'on bayroqning eng yomoni.** `tree` da ko'taradigan
   `limit` yo'q edi, ya'ni maslahat bajarilmas edi.
4. **Olib tashlashni qayd etuvchi izoh keyingi tekshiruvni buzadi.** `not in src`
   o'rniga **aniq ibora**ni tekshir.
5. **Bir xil shakl uch marta topildi** — II, 35, 36. Bu endi **naqsh**, tasodif emas.

## §142. Fazza o'ttiz yettinchi — sheets registri va **javob tashlab yuborgan qator**

### §142.1. Sirt — `sheets.py` (335 satr)

Operator e'lon qilgan Google Sheets registrlari: aktiv ierarxiyasi, vision, telephony
va workforce **hammasi shu qatlam orqali** o'qiydi.

| # | Chegara | Qiymat |
|---|---|---|
| 1 | `MAX_RESPONSE_BYTES` | 200 000 |
| 2 | `MAX_ROWS` | 200 |
| 3 | `MAX_CELL_CHARS` | 200 |
| 4 | `REGISTER_RE` / `RANGE_NAME_RE` | 1..64 |
| 5 | `SPREADSHEET_ID_RE` | 20..120 |
| 6 | `CONNECTION_RE` | 1..64 |
| 7 | A1 satr uzunligi | 128 (**himoyaviy**) |
| 8 | registrlar soni | 50 |
| 9 | range'lar soni | 40 |
| 10 | `max_rows` e'loni | 1..1000 |
| 11 | o'qish shifti | `min(e'lon, MAX_ROWS)` |

### §142.2. Nuqson A — **bir limit, ikki xil javob**

`_read` ikkala tool'ga ham xizmat qiladi: `sheets.read` (xom matritsa) va
`sheets.rows` (sarlavha bo'yicha kalitlangan obyektlar). `shape_rows` sarlavha
qatorini **hisobga oladi** (`values[:limit + 1]`), lekin **kalitlanmagan** yo'ldagi
matritsa kesimi hisobga olmagan:

```python
'returned': len(rows) if keyed else len(values[:limit]),
...
result['values'] = [... for row in values[:limit] if isinstance(row, list)]
```

`header_row` **sukut bo'yicha `True`**, ya'ni `values` ichida **ma'lumot bo'lmagan**
sarlavha qatori bor. O'lchandi (`limit=3`, `header_row=True`):

| Jadvaldagi ma'lumot qatorlari | `read` qaytargan qatorlar | `truncated` | |
|---|---|---|---|
| 2 | 2 | `False` | to'g'ri |
| **3** | **2** | **`False`** | **YOLG'ON** |
| 4 | 2 | `True` | kesish e'lon qilindi |
| 5 | 2 | `True` | kesish e'lon qilindi |

Bitta ifodada **uchta** xato:

1. Kesim `limit - 1` **ma'lumot** qatori qaytarardi, kalitlangan yo'l esa `limit` ta —
   ya'ni **bir xil `limit`, ikki xil hajm**.
2. Jadvalda **aynan `limit` ta** ma'lumot qatori bo'lganda **oxirgisi tashlab
   yuborilardi**, bayroq esa — ma'lumot qatorlarini sanaydi — `False` derdi.
   Chaqiruvchi *"hammasi shu"* deb aytilgan javobdan **bir qatorni yo'qotgan edi**.
3. `returned` bu yerda **matritsa** qatorlarini, u yerda **ma'lumot** qatorlarini
   sanardi: bitta maydon agent qaysi tool'ni chaqirganiga qarab **ikki xil** ma'no
   bildirardi.

Tuzatish: `header_offset = 1 if header_row else 0`, kesim `values[:limit + header_offset]`,
`returned` esa **ikkala** yo'lda ham ma'lumot qatorlarini sanaydi.

### §142.3. **Bu shaklning to'rtinchi uchrashi**

| Faza | Yolg'on bayroq |
|---|---|
| **II** | `len(result) >= limit` — aynan to'lishni "kesilgan" deb o'qigan |
| **35** | `truncated` **hech qachon hodisa bo'lmaydigan** qatorlarni sanagan |
| **36** | `truncated` **hech qanday sahifa ushlamagan** holda kesishni e'lon qilgan |
| **37** | `truncated` **tashlab yuborilgan qatorni** inkor etgan |

To'rttasi ham bir xil savolga noto'g'ri javob beradi: *"ko'proq bormi, qayta
so'raymi?"* — lekin **37 birinchisi bo'ldi**, unda bayroq kesishni **o'ylab topmaydi**,
balki **haqiqiy yo'qotishni inkor etadi**. Bu ikki xil yolg'on, va ikkinchisi
xavfliroq: birinchisi ortiqcha so'rovga, ikkinchisi **jimgina yo'qotishga** olib
keladi.

### §142.4. Nuqson B — `max_rows` e'loni har bir oddiy o'qishni yiqitardi

`registers` `max_rows` ni **1000 gacha** qabul qiladi, o'qish shifti esa `MAX_ROWS`.
E'lon qilingan qiymat to'g'ridan-to'g'ri `bounded_int(..., 1, MAX_ROWS)` ga
uzatilardi:

```
max_rows: 500 e'lon qilgan registr
  -> limit = None -> limit = 500
  -> bounded_int(500, 'limit', 1, 200)
  -> ValueError: limit must be an integer 1..200
```

Ya'ni **`limit` umuman bermagan** har bir oddiy o'qish yiqilardi — holbuki bu
konfiguratsiyani **`registers` o'zi qabul qilgan edi**. Konfiguratsiya qabul qilgan
qiymat keyingi bosqichda yiqitmasligi kerak. Tuzatish: **kesish, ko'tarish emas** —
`min(limit, MAX_ROWS)`.

Bu tuzatish `AUDIT-CORRECTIONS-UZ.md` dagi eski da'voni ham **falsifikatsiya qildi**
(qarang: §142.9).

### §142.5. Revert matritsasi — **22 tadan 1 tasi yashil** (halol)

| Guruh | Natija |
|---|---|
| S1–S18, S20–S22 (konstantalar, regex shiftlari, satr chegaralari, sanoq shiftlari, `header_row`/unknown-key tekshiruvlari, `_authorize`, uchala tuzatish) | **QIZIL** |
| **S19** (`shape_rows` ichki kesimi) | **YASHIL** — *halol, erishib bo'lmaydigan* |

S19 **haqiqatan erishib bo'lmaydigan**: `capped = values[:limit + 1]` allaqachon
`matrix` ni chegaralaydi, shuning uchun `matrix[1:]` va `matrix[1:limit + 1]` —
**bir xil ro'yxat**. U **himoyaviy ortiqchalik**: xulq bilan qadalmadi, **aniq qator
bilan** qadaldi va sababi manbada ham, sinovda ham izohda yozilgan.

**Birinchi yurgizishda 8 tasi yashil edi** — S1–S3 va S6–S12 (konstantalar sinovlar
tomonidan **moduldan qayta o'qilardi**, ya'ni mutatsiya **ikkala tomonni** ham
surardi) va S17/S18 (mening tuzatishlarim).

S4/S5 `PATTERN-MISSING (2)` berdi: `REGISTER_RE` va `RANGE_NAME_RE` — **bir xil
naqsh**, shuning uchun yalang'och literal **ikki marta** uchraydi va matritsa
mutatsiya qilishdan bosh tortdi. E'lon qatoriga bog'landi.

### §142.6. Qo'shilgan sinovlar (29 → 66)

`SheetsBoundaryTests` — mavjud sinovlar konstantani **moduldan qayta o'qiydi**
(`MAX_CELL_CHARS`, `shape_rows` ning o'z limiti), shuning uchun konstantani
kengaytirish **ikkala tomonni** surardi va sinov **yashil qolardi**. Yangi sinflar
**qiymatni** qadaydi:

- `test_the_read_ceiling_is_two_hundred`
- `test_the_cell_ceiling_is_two_hundred`
- `test_the_response_byte_ceiling_is_two_hundred_thousand`
- `test_the_spreadsheet_id_ceiling_is_one_hundred_and_twenty`
- `test_the_connection_name_ceiling_is_sixty_four`
- `test_register_and_range_names_are_capped_at_sixty_four`
- `test_the_range_length_ceiling_is_one_hundred_and_twenty_eight`
- `test_a_sheet_name_without_a_cell_is_refused`
- `test_the_register_cap_is_fifty`
- `test_the_range_cap_is_forty`
- `test_the_declared_max_rows_ceiling_is_one_thousand`
- `test_a_header_is_not_counted_as_a_data_row`
- `test_a_register_may_declare_max_rows_above_the_read_ceiling`
- `test_shape_rows_returns_at_most_the_limit`
- `test_the_keyed_row_slice_is_defensively_bounded`

Probe: **977 → 1043** xossa.

### §142.7. **O'zim qilgan xatolar**

1. **`returned` ni ma'lumot qatori deb o'yladim** — kalitlanmagan yo'lda u
   **matritsa** qatorlarini sanaydi. Aynan shu **noto'g'ri taxmin nuqsonni ochdi**:
   sinov yiqildi va o'lchashga majbur qildi. Xato qilish — o'lchashning boshlanishi.
2. **Birinchi matritsada S4/S5 ni "yashil" deb o'qidim** — aslida ular
   **o'lchanmagan** edi. `PATTERN-MISSING` — **natija emas**.
3. **S19 ni tuzatishga urindim**, holbuki u nuqson emas, balki himoyaviy
   ortiqchalik edi. Uni **o'lchov bilan** aniqladim, taxmin bilan emas.

### §142.8. Saboqlar

1. **Bir xil `limit` — bir xil ma'no.** Ikkala read tool bir xil hajm qaytarishi
   kerak: sarlavha qatorini **kesimda hisobga ol**, **bayroqda chiqarib tashla**.
2. **Bayroq yo'qotishni ham inkor eta oladi.** Ilgari bayroq kesishni **o'ylab
   topardi**; endi u **haqiqiy yo'qotishni** yashirdi. Ikkalasi ham tekshirilsin:
   *"ushlandimi?"* **va** *"hech narsa tushib qolmaganmi?"*.
3. **Konstantani moduldan qayta o'qiydigan sinov mutatsiyani ushlamaydi.** `MAX_ROWS`
   ni sinash uchun **literal** kerak, import emas. Bu — "simvoldan yasalgan sinov
   shiftni isbotlamaydi" ning yana bir ko'rinishi.
4. **Erishib bo'lmaydigan qo'riqchi — nuqson emas, lekin qadalishi shart.** Uni
   **aniq qator** bilan qada va **sababini** yoz, aks holda keyingi o'quvchi uni
   o'lik kod deb o'chirib tashlaydi.
5. **`PATTERN-MISSING` — yashil emas, o'lchanmagan.** Matritsa uni **alohida**
   ko'rsatishi kerak.

### §142.9. **Eski da'vo falsifikatsiya qilindi**

`AUDIT-CORRECTIONS-UZ.md` da ikki jumla bor edi, ikkalasi ham **shu fazada
o'lchanib, noto'g'ri chiqdi**:

| Eski da'vo | O'lchangan haqiqat |
|---|---|
| "`sheets` `truncated: len(values) > limit` ni **to'g'ri** hisoblaydi" | **Noto'g'ri** — sarlavha qatorini ma'lumot sifatida sanardi (§142.2) |
| "`max_rows` ni 200 dan oshirish **rad etiladi**" | `registers` **qabul qilardi**, keyin **har bir oddiy o'qish** yiqilardi (§142.4) |

Ikkalasi ham tuzatildi. **Saboq: "tekshirilgan va sog'lom topilgan" ro'yxati ham
eskirishi mumkin** — u o'lchov natijasi, qaror emas.


## §143. Fazza o'ttiz sakkizinchi — hujjat qatlami va **uchta qo'riqchi noto'g'ri narsani o'lchagan**

### §143.1. Sirt — `documents.py` (1023 satr)

Hisob-kitob (accounts-payable) nazorat zanjiri. Modul o'z docstring'ida shunday deydi:

> Money is compared in integer minor units (tiyin), **never in floats**, because
> ``0.1 + 0.2 != 0.3`` is not an acceptable property of an accounts-payable check.

| # | Chegara | Qiymat |
|---|---|---|
| 1 | `MAX_AMOUNT` | 10¹⁵ |
| 2 | `MAX_LINE_ITEMS` | 200 |
| 3 | `MAX_TEXT_CHARS` | 200 |
| 4 | `NUMBER_RE` / `PARTY_RE` | 1..64 |
| 5 | `MINOR_UNITS` | UZS 1 · USD/EUR/RUB 100 |
| 6 | pul satri shakli | 1..15 butun, 1..6 kasr |
| 7 | `bank_account` | **4..34 raqam** |
| 8 | `supplier_history` | 1..500 (kesiladi) |
| 9 | `tolerance_minor` (tool) | 0..100 000 000 000 |
| 10 | `outlier_ratio` (tool) | 1..100 |
| 11 | JSON payload | 16 000 belgi |
| 12 | `ROUND_TRAILING_ZEROS` | 3 |
| 13 | `DEFAULT_TOLERANCE_MINOR` | 0 |

### §143.2. Nuqson A — qo'riqchi **boshqa satrni** chegaralagan

`bank_account` shunday tekshirilardi, **keyin** bo'shliqlar olib tashlanardi:

```python
if not re.fullmatch(r'[0-9 ]{4,34}', bank_account):
    raise ValueError('bank_account must be 4 to 34 digits')
bank_account = bank_account.replace(' ', '')
```

Ya'ni chegara **bo'shliqli** satrga qo'yilgan, xabar esa **raqamlarni** va'da qiladi.
O'lchandi:

| Qiymat | Natija |
|---|---|
| `'    '` (4 bo'shliq) | mos keldi → `''` bo'lib saqlandi |
| `'1   '` | mos keldi → **bir raqamli** hisob saqlandi |
| `'12  '` | mos keldi → `'12'` |

**Bo'shatilgan qiymat — eng xavflisi.** `fraud_signals` `bank_account_changed`
signalini `if doc['bank_account']:` bilan darvozalaydi, va u signal **MATERIAL** —
u yetkazib beruvchi hisob raqamini almashtirishni ushlash uchun bor, ya'ni
hisob-kitob sohasidagi klassik firibgarlik. Faqat bo'shliqdan iborat maydon nazoratni
**yiqitmadi — uni olib tashladi**. Endi xabar aytgan raqamlar chegaralanadi.

### §143.3. Nuqson B — shakl tekshiruvi **sana** tekshiruvi o'rniga o'tgan

`[0-9]{4}-[0-9]{2}-[0-9]{2}` — bu shakl. `2026-13-45`, `2026-02-30`, `2026-00-00`
hammasi mos keladi. Qiymat **saqlanardi**, keyin `date()` unga **`fraud_signals`
ichida** xato berardi — u yerda istisno tutiladi, shuning uchun hujjat hech qanday
hafta kuni signalisiz qolardi va **hech narsa sababini aytmasdi**.

Docstring'ning o'zi shunday dalil keltiradi: *"10.01.2026 ni yanvar yoki oktabr deb
taxmin qilish takrorlanish oynasini oylarga suradi, shuning uchun noaniq shakl rad
etiladi"*. Noaniq sana rad etilardi — **mumkin bo'lmagan** sana qabul qilinardi, holbuki
u yerda taxmin qiladigan narsa **yo'q**.

### §143.4. Nuqson C — pul **float** orqali o'tgan, uni taqiqlagan yagona modulda

```python
import statistics
return int(statistics.median(values))
```

`statistics.median` **juft sonda bo'ladi** — ya'ni float qaytaradi. Float esa ketma-ket
butun sonlarni faqat **2⁵³ gacha** aniq saqlaydi, `MAX_AMOUNT` esa USD uchun
**10¹⁷** minor birlikka yo'l qo'yadi. O'lchandi (tuzatishdan oldin):

| Chaqiruv | Natija | Aniq qiymat | Xato |
|---|---|---|---|
| `_median([10**16, 10**16+2])` | 10¹⁶ | 10¹⁶ + 1 | **−1** |
| `_median([10**17, 10**17+2])` | 10¹⁷ | 10¹⁷ + 1 | **−1** |

Va bu son approver'ning daliliga **"the median"** deb chop etiladi. Modulning o'z
docstring'i, aynan ikki paragraf yuqorida, shunday deydi:

> A number labelled as the median has to be the median, or the label is a lie and the
> approver is checking the flag against a figure it cannot reconcile.

Ya'ni **yuqori-o'rta xatoni tuzatish float'ni kiritdi**, float esa yorliqni
2⁵³ dan yuqorida **yana yolg'onga aylantirdi**. Endi juft son **butun arifmetikada**
o'rtachalanadi — har qanday kirish uchun aniq va chegarani baribir **ko'tarmaydi**.

### §143.5. Bir assimetriya — tuzatilmadi, **qayd etildi**

Pul **satri** shakli 15 butun raqamga yo'l qo'yadi, ya'ni **10¹⁵ ning o'zini**
chiqarib tashlaydi; **oraliq** tekshiruvi va **int** yo'li esa uni qabul qiladi. Shakl
chegarasi oraliq chegarasidan **bir raqam qattiqroq**. Shu sababli ajratuvchi kirish —
**aynan `10**15`**: 16 ta to'qqizdan iborat toshqin ikkala holatda ham oraliq
tekshiruvi tomonidan rad etiladi va shakl haqida **hech narsa isbotlamaydi**.

### §143.6. Revert matritsasi — **26 tadan 18 tasi yashil**

| Guruh | Natija |
|---|---|
| D8, D12, D13, D17, D23, D24, D25 (manfiy summa, yumaloqlik, tolerance, yuqori-o'rta, `>=`, UZS birligi, o'z-o'zini chiqarish) | **QIZIL** |
| D1–D7, D9–D11, D14–D16, D18–D22, D26 | **YASHIL** |

Tuzatishdan keyin **26/26 qizil**, `PATTERN-MISSING` **0**, bayt-darajali tiklash
**True**.

**D18** va **D21** dastlab `PATTERN-MISSING` berdi: D21 dagi satr **ikki joyda**
(`documents_config` va `_plan_tool`) bir xil, D18 naqshi esa manbada **qator
uzilishi** bor edi. Ikkalasi ham bog'landi.

**D6 qiziq:** mutatsiya **yashil** edi, chunki mening sinovim 16 ta to'qqizni
sinardi — uni **oraliq** tekshiruvi rad etadi, shakl emas. Ajratuvchi kirish
`'1000000000000000'` (aynan 10¹⁵). Sinov tuzatildi.

### §143.7. Qo'shilgan sinovlar (100 → 117)

`BoundaryTests` — mavjud to'plam **xulq**ni tekshiradi, bu sinflar **qiymatni**
qadaydi:

- `test_the_amount_ceiling_is_ten_to_the_fifteenth`
- `test_the_line_item_cap_is_two_hundred`
- `test_the_text_cap_is_two_hundred`
- `test_the_identifier_caps_are_sixty_four`
- `test_the_amount_string_bounds`
- `test_a_negative_amount_is_refused`
- `test_the_bank_account_bound_is_on_the_digits_not_the_spaces`
- `test_an_emptied_bank_account_cannot_disable_the_signal`
- `test_the_date_guard_checks_the_calendar_not_only_the_shape`
- `test_the_median_is_exact_above_two_to_the_fifty_third`
- `test_the_median_does_not_route_money_through_a_float`
- `test_the_tool_bounds_are_pinned`
- `test_the_payload_bound_is_sixteen_thousand`
- `test_the_config_floors`
- `test_the_history_clamp_is_pinned`
- `test_the_round_number_threshold_is_three`
- `test_the_default_tolerance_is_zero`

Probe: **1043 → 1147** xossa.

### §143.8. **O'zim qilgan xatolar**

1. **Int yo'lini minor birlik deb o'yladim.** `_amount_minor(10**17, 'USD')` ni
   "qabul qilinadi" deb kutdim — aslida **int — major birlik** va 100 ga
   ko'paytiriladi, shuning uchun `10**19 > 10**17` bo'lib rad etiladi. Kutilmam
   noto'g'ri edi, kod emas.
2. **`supplier_history` ni rad etadi deb o'yladim** — u **kesadi**. Bu to'g'ri shakl.
3. **16 ta to'qqiz bilan shakl chegarasini sinadim** — uni oraliq tekshiruvi ushlaydi,
   ya'ni sinov **noto'g'ri chegara** haqida edi (D6 yashil qoldi).
4. **`'statistics' not in source`** yozdim — yangi docstring `statistics.median` ni
   **nomlaydi**, shuning uchun so'z olib tashlashdan keyin ham qoladi. Bu
   `MAX_QUANTITY` (26) va `tree` bayrog'i (36) tuzog'i — **beshinchi marta**.

### §143.9. Saboqlar

1. **Xabar aytgan narsani chegarala.** `'4 to 34 digits'` degan xabar **raqamlarni**
   va'da qiladi; bo'shliqli satrni chegaralash boshqa miqdorni o'lchaydi. Bu
   §142 dagi `bank_account` bilan bir oila: *qo'riqchi o'zi haqida aytgan gap bilan
   o'lchanishi kerak*.
2. **Bo'shatilgan maydon — o'chirilgan nazorat.** Agar signal `if value:` bilan
   darvozalansa, qiymatni **bo'shatib bo'lmaydigan** qil, aks holda maydon nazoratni
   yiqitmaydi, **olib tashlaydi** — va bu jimgina bo'ladi.
3. **Shakl tekshiruvi sana tekshiruvi emas.** `[0-9]{2}` oyni ifodalamaydi. Qabul
   qilingan qiymat keyin **istisno tutiladigan joyda** yiqilsa, natija — **jim
   o'tkazib yuborilgan signal**.
4. **Modul o'z qoidasini e'lon qilsa, uni har joyda tekshir.** `documents.py` "never
   in floats" deydi; `_median` yagona istisno edi va u **dalilga chop etiladigan**
   sonni ishlab chiqarardi. E'lon qilingan qoidani **grep qil**, ishonma.
5. **Sinov noto'g'ri chegara haqida bo'lsa, yashil qoladi.** Ikki chegara yaqin
   bo'lsa (shakl 15 raqam, oraliq 10¹⁵), sinov **ikkalasini ajratadigan** kirishni
   tanlashi kerak — aks holda u kuchliroq chegarani o'lchaydi va kuchsizini
   o'lchamaydi.
6. **Olib tashlashni qayd etuvchi izoh keyingi tekshiruvni buzadi** — bu
   **beshinchi** uchrash. `not in source` o'rniga **aniq kod**ni tekshir.


## §144. Fazza o'ttiz to'qqizinchi — CRM chegarasi va **ochiq turgan yagona darvoza**

### §144.1. Sirt — `crm_contract.py` (487) + `crm_gateway.py` (368)

Har bir CRM drayveri bo'lishadigan validatsiya qatlami: Bitrix24, Kommo/amoCRM, 1C va
operator e'lon qilgan custom HTTP ulanishi. Bu yerda noto'g'ri chegara — **hammasi**
uchun noto'g'ri.

| # | Chegara | Qiymat |
|---|---|---|
| 1 | `bounded_price` | 0..10¹² |
| 2 | `duration_seconds` | 0..86 400 |
| 3 | `scheduled_at` | 0..10¹² |
| 4 | telefon raqamlari | `+` bilan 7..15 |
| 5 | email | 254 belgi |
| 6 | valyuta | **aniq** 3 harf |
| 7 | yo'l (`safe_relative_path`) | 512 belgi |
| 8 | pointer segmenti | 1..64 |
| 9 | pointer chuqurligi | 4 |
| 10 | `CUSTOM_HTTP_METHODS` | GET · POST · PUT · PATCH |
| 11 | `CUSTOM_HTTP_PLACEHOLDERS` | 8 nom |
| 12 | `IMPLEMENTED_CRM_DRIVERS` | 5 drayver |
| 13 | `QUERY_SEARCH_DRIVERS` | 4 drayver |

### §144.2. Nuqson A — CRM uchun **yagona darvoza** siyosatni **yumshoq** o'qigan

`crm_gateway._check_agent_allowed` shunday yozgan edi:

```python
if allowed_conns and connection not in allowed_conns:
```

Ya'ni **bo'sh** `allowed_connections` — boshqa hamma modul "hech narsa ruxsat
etilmagan" deb o'qiydigan qiymat — bu yerda **"hammasi ruxsat etilgan"** bo'lardi.
O'lchandi (`allowed_connections: []`): `_check_agent_allowed` jimgina qaytdi va
`crm.lead.search` **adapterga yetib bordi** va o'qishni amalga oshirdi.

Bu **aynan shu yerda** eng muhim, chunki CRM ulanishi **faqat shu joyda** tekshiriladi.
Engine'ning o'z allowlist tekshiruvi
`{'connectors.read', 'database.read', 'database.plan_write', 'database.write'}` ni
qamraydi va **birorta `crm.*` tool** o'sha to'plamda **yo'q** — ya'ni yumshoq shakl
"ikkinchi fikr" emas edi, u **yagona darvoza** edi va u **ochiq turardi**.

| Modul | Shakl |
|---|---|
| `engine.py` · `connectors.py` (×2) · `database/gateway.py` · `google_adapters.py` · `oauth.py` · `business_graph.py` · `sheets.py` | **qat'iy** |
| **`crm/crm_gateway.py`** | **yumshoq** ← yagona |

`sheets.py` aynan shu sababdan §137 da tuzatilgan edi. Bu — runtime'dagi **oxirgi
yumshoq o'quvchi**.

### §144.3. Nuqson B — yalang'och `int()` **validatsiya emas**

```python
duration = int(data.get('duration_seconds', 0))
```

O'lchandi: `duration_seconds=None` → **`TypeError`** (kontrakt `ValueError` deydi);
`1.9` → **1** (jimgina qisqartirilgan). Va `bounded_price` xuddi shunday: `1.9` → **1**,
ya'ni kasr narx **jimgina kichraytirilardi**; `float('inf')` → **`OverflowError`** —
`ValueError` emas. Xabar esa "must be an integer amount" der edi.

Yangi `bounded_int` bool'ni, float'ni va satrni rad etadi, **ikki uchini ham**
chegaralaydi va faqat `ValueError` ko'taradi.

### §144.4. Nuqson C — yalang'och, **chegaralanmagan** `float()` NaN va inf'ni qabul qilgan

```python
scheduled_at = float(data.get('scheduled_at', 0))
```

`'nan'` va `'inf'` — **haqiqiy float**, shuning uchun ular **xato bermaydi**, va
quyida ularni rad etadigan hech narsa yo'q: qiymat audit yozuviga va chaqiruvchiga
qaytadi. O'lchandi: `scheduled_at='nan'` qabul qilindi va **`nan` bo'lib qoldi**.
Manfiy jadval ham qabul qilinardi.

Tool sxemasi bu maydonni **`{'type': 'integer', 'minimum': 0}`** deb e'lon qiladi,
shuning uchun parser endi **butun son** qaytaradi, shift esa — registry
validator'ining **o'z butun-son standarti** (`10 ** 12`), bu yerda o'ylab topilgan son
emas.

### §144.5. Bir nomzod **o'lchandi va rad etildi**

`tool_crm_lead_search` limitni yalang'och `int(args.get('limit', 20))` bilan
o'giradi — chegara yo'q, shuning uchun `10 ** 9` adapterga **yetib boradi**. Lekin u
**ikki marta yutiladi**: tool sxemasi `1..50` ni e'lon qiladi, va **har bir adapter**
`min(max(1, limit), 50)` bilan yana kesadi. Ya'ni bu chegara buzilishi **emas**, va u
"tuzatildi" emas — **o'lchandi** deb yozildi.

### §144.6. Ikki sirt **qayd etildi, o'zgartirilmadi**

`safe_relative_path` traversal, `//`, teskari chiziq, bo'shliq, qo'shtirnoq, `:` va
`#` ni rad etadi — lekin **`?`** so'rov ajratuvchisini va **percent-encoding**ni
(`/a%2e%2e%2fb`) **qabul qiladi**. Docstring'ning da'vosi so'rovni **qayta
nishonlash** haqida, shablon esa **operator konfiguratsiyasi**, agent kiritmasi emas;
placeholder **qiymatlari** doim bo'sh safe-set bilan quote qilinadi. Ochiq risk
sifatida yozildi.

### §144.7. Revert matritsasi — **30 tadan 25 tasi yashil**

| Guruh | Natija |
|---|---|
| C2, C6, C7, C14, C26 (darvoza o'chirilishi, narx, shift, 8-shoxobcha, drayverlar) | **QIZIL** |
| C1, C3–C5, C8–C13, C15–C25, C27–C30 | **YASHIL** |
| **C1 (tuzatishni qaytarish)** | **YASHIL** ← eng muhimi |

Tuzatishdan keyin **30/30 qizil**, `PATTERN-MISSING` **0**, bayt-darajali tiklash
**True**.

**C1 yashil bo'lishi eng katta bo'shliq edi:** `test_connection_outside_agent_allowlist_is_denied`
**bo'sh bo'lmagan** allowlist ishlatadi, ya'ni **bo'sh** holat umuman sinovsiz edi.
Xavfsizlik tuzatishi **qayta kiritilishi mumkin** edi.

**C30** dastlab `PATTERN-MISSING (0)` berdi: mutatsiya `b"..."` bayt literali ichida
`\u0412` yozgan edi, va bayt literalida `\u` **ishlanmaydi**. Aniq UTF-8 baytlar
qo'yildi.

### §144.8. Qo'shilgan sinovlar (13 → 33; gateway 9 → 23)

`CRMBoundaryTests` (`test_crm_contract.py`):

- `test_bounded_int_refuses_everything_that_is_not_an_int`
- `test_bounded_int_walks_both_ends`
- `test_the_price_is_an_integer_amount`
- `test_the_call_duration_ceiling`
- `test_the_schedule_is_a_bounded_integer`
- `test_the_phone_digit_floor_is_seven`
- `test_the_phone_digit_ceiling_is_fifteen`
- `test_the_998_and_leading_8_branches_are_length_exact`
- `test_the_email_ceiling_is_two_hundred_and_fifty_four`
- `test_the_currency_is_exactly_three_letters`
- `test_the_path_ceiling_is_five_hundred_and_twelve`
- `test_the_path_rejects_every_retargeting_form`
- `test_the_placeholder_allowlist_is_exact`
- `test_the_http_method_allowlist_is_exact`
- `test_a_custom_http_config_is_normalised`
- `test_the_pointer_segment_ceiling_is_sixty_four`
- `test_the_pointer_depth_ceiling_is_four`
- `test_the_driver_sets_are_exact`
- `test_every_status_map_lands_on_a_canonical_status`
- `test_every_reverse_map_round_trips`

`CRMAllowlistBoundaryTests` (`test_crm_gateway.py`):

- `test_an_empty_allowlist_permits_no_connection`
- `test_an_absent_allowlist_permits_no_connection`
- `test_the_adapter_is_never_reached`
- `test_a_tool_outside_the_policy_is_denied`
- `test_a_permitted_connection_still_reaches_the_adapter`

Probe: **1147 → 1298** xossa.

### §144.9. **O'zim qilgan xatolar**

1. **`bounded_int(None)` ni rad etadi deb o'yladim** — u **default** qaytaradi. Bu
   modulning o'z an'anasi (`bounded_price(None) == 0`, `clean_text(None) == ''`), ya'ni
   kod emas, **kutilmam** noto'g'ri edi.
2. **`CUSTOM_HTTP_OPERATIONS` ga placeholder ro'yxatini yozdim** — ikkalasi ham 8/7
   elementli to'plam, men noto'g'ri birini ko'chirdim.
3. **`dig(..., 'a.a.a.a')` ni skalyar deb o'yladim** — u **to'rtinchi** darajadagi
   obyektni qaytaradi; skalyar uchun chuqurlik bir kam bo'lishi kerak.
4. **`dig({'a-b': 1}, 'a-b')` ni `None` deb o'yladim** — chiziqcha **ruxsat etilgan**,
   shuning uchun kalit topiladi va `1` qaytadi.

### §144.10. Saboqlar

1. **Siyosat qiymatini hamma o'quvchi bir xil o'qishi kerak.** Bu naqsh **ikkinchi**
   marta topildi (§137 `sheets`, §144 `crm_gateway`) — va tuzatish **bittasiga**
   qo'llanib, **qo'shnisi** qolib ketdi. Tuzatishdan keyin **grep qil**: bir xil
   qiymatni o'qigan **hamma** joyni sanang.
2. **Yalang'och `int()`/`float()` — validatsiya emas.** `int(True)` = 1, `int(1.9)` = 1,
   `int(None)` = `TypeError`, `int(inf)` = `OverflowError`; `float('nan')` **xato
   bermaydi**. Ikkalasi ham kontraktdan tashqari istisno yoki **jimgina tahrir**
   beradi.
3. **"Darvoza" ni sanang.** Yumshoq shakl "ehtimol zararsiz" ko'rinadi — faqat u
   **yagona** tekshiruv bo'lsa, u **butun nazorat**. Har bir himoya uchun so'ra: *agar
   bu o'chsa, yana kim ushlaydi?* Javob "hech kim" bo'lsa, u **kritik**.
4. **Sxema va parser — bir xil chegaraning ikki bayoni.** Ular kelishmasa, **qaysi
   biri ishlaydi?** Tool yo'lida sxema; to'g'ridan-to'g'ri chaqiruvda parser. Ikkalasi
   ham o'zi yetarli bo'lishi kerak.
5. **Sonni o'ylab topma — validator'ning standartini ishlat.** `scheduled_at` shifti
   `10 ** 12`: bu `validate_schema` ning o'z butun-son standarti, taxmin emas.
6. **Bayt literali `\u` ni ishlamaydi.** Mutatsiya naqshini faylning **haqiqiy
   baytlaridan** ol.


## §145. Fazza qirqinchi — to'rt CRM adapteri va **to'rt marta yozilgan chegara**

### §145.1. Sirt — `bitrix24` (269) · `kommo` (249) · `onec` (301) · `custom_http` (298)

To'rt drayver **bir xil to'rt chegarani** alohida-alohida yozadi. Har biri o'z testiga
ega, lekin **umumiy** chegaralar hech qayerda qadalmagan.

| # | Chegara | Qiymat |
|---|---|---|
| 1 | `MAX_RESPONSE_BYTES` | 80 000 |
| 2 | limit clamp | 1..50 |
| 3 | `timeout_seconds` | 1..60 |
| 4 | provider satri konversiyasi | `optional_*` / `provider_price` |

### §145.2. Nuqson A — **ishonchsiz provider maydoni butun o'qishni yiqitardi**

`onec` va `custom_http` ning `_shape_lead` / `find_contacts` i `normalize_phone` va
`normalize_email` ni chaqirardi — ular **xato ko'taradi**. O'lchandi (bitta buzuq qator,
yaxshi qatorlar yonida):

| Chaqiruv | Qiymat | Natija |
|---|---|---|
| `onec find_leads` | `phone: 'n/a'` | **`ValueError: Empty phone number`** |
| `custom_http find_leads` | `phone: 'n/a'` | **`ValueError: Empty phone number`** |
| `onec find_leads` | `email: 'not-an-email'` | **`ValueError: Invalid email address`** |
| `onec find_contacts` | `phone: 'n/a'` | **`ValueError: Empty phone number`** |

Ya'ni **bitta buzuq lead yonidagi hamma yaxshi leadni yo'q qilardi**. Va bu o'sha
adapterlarning **o'z dizayniga zid**: `_rows` dict bo'lmagan qatorni **filtrlaydi**,
`_map` esa **default** qaytaradi — aynan provider shakli siljishiga toqat qilish uchun.

`optional_phone` / `optional_email` qatorni **saqlaydi**, yaroqsiz maydonni bo'shatadi,
`id` va `name` ni qoldiradi — shuning uchun u **aniqlanadigan** bo'lib qoladi.

### §145.3. Nuqson B — provider narxi **`int(float(...))`** bilan o'girilardi

Bitrix24 `int(float(item.get('OPPORTUNITY') or 0))`, Kommo `int(item.get('price') or 0)`.
O'lchandi:

| Qiymat | Natija |
|---|---|
| `'abc'` | `ValueError` (butun qidiruvni yiqitadi) |
| `'nan'` | `ValueError` |
| `'inf'` | **`OverflowError`** (`ValueError` emas) |
| `'450000.99'` | **450000** (binary float orqali jimgina qisqartirilgan) |

Ya'ni **pul float orqali o'tgan** — bu paket boshqa joyda taqiqlaydi (§143 aynan shu
haqda) — va bitta buzuq narx butun qidiruvni yiqitgan. `provider_price` qiymatni
chegaralaydi, **float orqali o'tkazmaydi**, yaroqsiz qiymat uchun **0** qaytaradi, kasr
narxni esa **avvalgidek qisqartiradi** (hujjat qatlami allaqachon yaxlitlash emas,
qisqartirishni tanlagan).

### §145.4. Nuqson C — `timeout_seconds` **ikkitasi uchun bor, ikkitasi uchun yo'q**

O'lchandi (`timeout_seconds: 45`):

| Adapter | Transport olgan `timeout` |
|---|---|
| `onec` | **45** |
| `custom_http` | **45** |
| `bitrix24` | **umuman uzatilmagan** → transport default 15 |
| `kommo` | **umuman uzatilmagan** → transport default 15 |

`bitrix24` va `kommo` bu kalitni **o'qimaydi ham, tekshirmaydi ham**. Va diapazondan
tashqari qiymat (`0`, `61`, `'x'`, `10**9`) **jimgina qabul qilinardi**. Ya'ni **bitta
konfiguratsiya kaliti to'rttasi uchun ikki xil ma'no** bildirardi.

### §145.5. Nuqson D — **hajm rad etilishi** "transport xatosi" deb xabar qilinardi

80 000 chegarasi **to'rttasida ham** amalda. Lekin rad etish `try` **ichida** ko'tariladi
va quyidagi keng `except Exception` uni **tutib oladi**. `onec` va `custom_http`
o'z turini **qayta ko'taradi**; `bitrix24` va `kommo` **yo'q**. O'lchandi (80 001 bayt):

| Adapter | Xabar |
|---|---|
| `bitrix24` | `Bitrix24HTTPError: Bitrix24 **transport error**: Bitrix24 response exceeded maximum allowed bytes (80KB)` |
| `onec` | `OneCHTTPError: 1C response exceeded maximum allowed bytes (80KB)` |

Birinchisini o'qigan operator **tarmoq nosozligini** qidiradi.

### §145.6. Bir assimetriya — tuzatilmadi, **qayd etildi**

`provider_price` ning **satr** shakli 15 butun raqamga yo'l qo'yadi, ya'ni **10¹⁵ ning
o'zini** chiqarib tashlaydi; **butun son** shoxobchasi esa uni qabul qiladi. Bu §143 da
hujjat qatlami uchun qayd etilgan **bir raqamli assimetriyaning** aynan o'zi.

### §145.7. Revert matritsasi — **27 tadan 21 tasi yashil**

| Guruh | Natija |
|---|---|
| E20–E24, E26, E27 (onec/custom clamp, `{lead_id}`, Host override, timeout shifti) | **QIZIL** |
| E1–E19, E25 | **YASHIL** |

Tuzatishdan keyin **27/27 qizil**, `PATTERN-MISSING` **0**, bayt-darajali tiklash
**True**.

**Ikki mutatsiya noto'g'ri sababdan qizil edi va qayta kesildi.** E12/E13 ni birinchi
urinishda `except ...: pass` + `if False:` bilan yozdim — bu **sintaksis xatosi**, ya'ni
rejim xossani o'lchamasdan qizargan. Endi qo'riqchi bloki **butunlay olib tashlanadi**.

**E18–E21** `PATTERN-MISSING` berdi: limit clamp **har bir `find_*` metodida** takrorlanadi
(kommo 2 marta, onec va custom_http 3 marta). Matritsaga `replace_all` rejimi qo'shildi —
bir xil chegarani **hamma joyda** mutatsiya qiladi va nechta almashtirganini **chop
etadi**.

### §145.8. Qo'shilgan sinovlar — yangi fayl, **13 test**

`runtime_tests/test_crm_adapter_boundaries.py` — har adapterning o'z to'plami bor, bu
fayl **to'rttasi birgalikda** aytadigan chegaralarni qadaydi:

- `test_the_provider_normalisers_tolerate_what_they_cannot_parse`
- `test_provider_price_returns_zero_for_anything_unusable`
- `test_provider_price_bounds`
- `test_provider_price_truncates_a_fraction_rather_than_refusing`
- `test_provider_price_does_not_route_money_through_a_float`
- `test_a_malformed_provider_row_does_not_abort_the_search`
- `test_a_malformed_provider_price_does_not_abort_the_search`
- `test_the_response_byte_ceiling_is_eighty_thousand`
- `test_the_byte_ceiling_is_enforced_and_keeps_its_own_message`
- `test_the_limit_clamp_is_fifty`
- `test_the_limit_clamp_holds_at_the_boundary`
- `test_the_timeout_is_read_validated_and_passed`
- `test_the_header_name_ceiling_is_sixty_four`

Probe: **1298 → 1431** xossa.

### §145.9. **O'zim qilgan xatolar**

1. **Response-map pointer'i hal qilinmadi.** `'phone': 'Phone'` deb yozdim, qatorda esa
   `'phone'` bor edi — shuning uchun `_map` `None` qaytardi va **buzuq qiymat umuman
   ko'rinmadi**. Birinchi o'lchov "nuqson yo'q" dedi; bu **noto'g'ri salbiy** edi va
   faqat to'g'ri pointer bilan qayta o'lchaganda chiqdi.
2. **E12/E13 ni sintaksis xatosi bilan "qizil" qildim** — xossani o'lchamagan mutatsiya
   natija emas. Qayta kesildi.
3. **`provider_price(str(10**15))` ni 10¹⁵ deb kutdim** — satr shakli 16 raqamni rad
   etadi. Bu mening taxminim xatosi edi, kod emas (§145.6).
4. **Bitta `python -c` one-liner'da teskari belgi** shell tomonidan buyruq sifatida
   bajarildi va izohni buzdi — matn qo'lda tiklandi.

### §145.10. Saboqlar

1. **Bir xil chegarani to'rtta faylda yozsang, to'rttasi ajralib ketadi.** `timeout_seconds`
   ikkitasi uchun bor, ikkitasi uchun yo'q edi; 80 000 chegarasi to'rttasida bor, lekin
   xato xabari ikkitasida boshqa. **Umumiy chegarani bir joyda** yoz yoki **hammasini
   birga** o'lcha.
2. **Ishonchsiz satr maydonini ko'taradigan funksiya bilan normallashtirma.** `_rows`
   filtrlaydi, `_map` default qaytaradi — ya'ni toqat **dizayn qarori**; ko'taradigan
   normalizator o'sha qarorni **bir qatorda bekor qiladi** va yonidagi hamma ma'lumotni
   yo'qotadi.
3. **Provider maydonini yalang'och `int()`/`float()` bilan o'girma.** Bu §144 va §143
   darsining **uchinchi** uchrashi: `int(True)`=1, `int(1.9)`=1, `int(inf)`=`OverflowError`,
   `float('nan')` esa **xato bermaydi**.
4. **`except Exception` qo'riqchining o'z istisnosini yutadi.** Rad etish `try` ichida
   ko'tarilsa, `except <O'zXato>: raise` **shart** — aks holda hajm rad etilishi
   "transport xatosi" bo'lib ko'rinadi. `sheets.py` va `onec`/`custom_http` buni
   qiladi; `bitrix24`/`kommo` qilmagan edi.
5. **Bir xil literal bir necha marta uchrasa, mutatsiya `PATTERN-MISSING` beradi.**
   `replace_all` rejimi qo'shildi va **nechta almashtirganini chop etadi** — jim
   "yashil" dan farqli.
6. **Noto'g'ri sababdan qizil — natija emas.** Sintaksis xatosi bilan qizargan mutatsiya
   xossani o'lchamaydi; uni **qayta kes**.


## §146. Fazza qirq birinchi — qayta aloqa sikli va **va'da qilingan talabni tekshirmagan qo'riqchi**

### §146.1. Sirt — `reengagement.py` (430 satr)

Avtonom qayta aloqa koordinatori: **haqiqiy mijozlarga** jadval bo'yicha yozadi. Shuning
uchun uning chegaralari — *mashina bir odamga necha marta yozishi mumkin* degan savolning
javobi.

| # | Chegara | Qiymat |
|---|---|---|
| 1 | `inactive_minutes` | 1..20 160 |
| 2 | `cooldown_seconds` | 300..2 592 000 |
| 3 | `max_attempts` | 1..10 |
| 4 | `max_per_cycle` | 1..20 |
| 5 | `interval_seconds` | 300..604 800 |
| 6 | `max_steps` | 1..12 |
| 7 | `max_seconds` | 60..86 400 |
| 8 | `POLICY_ID_RE` | 1..64 |
| 9 | ledger `limit` | 1..500 (kesiladi) |
| 10 | run input | 4 000 belgi |
| 11 | `last_error` | 200 belgi |

### §146.2. Nuqson — qo'riqchi **"is required"** derdi, bo'shliq esa o'tardi

```python
def _identifier(value, name, maximum=64):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f'{name} is required')
    return value.strip()
```

Tekshiruv **xom** qiymatni bo'shligiga qaraydi, qaytaradi esa **kesilganini**. O'lchandi:

| Chaqiruv | Natija |
|---|---|
| `_identifier(' ', 'agent')` | **`''`** |
| `_identifier('   ', 'connection')` | **`''`** |
| `_identifier('\t', 'actor')` | **`''`** |

Xabar *"is required"* deydi — va **bo'shliq uni qanoatlantiradi**. `configure` orqali
natija kosmetik emas: `configure(agent=' ')` **agenti bo'sh satr bo'lgan jadvalni
saqladi**, va har bir sikl tool darvozasida yiqilib, siklni **tushunarsiz
`feed_denied:Forbidden`** bilan o'chirardi — operatorning xatosi *"rad etish"* emas,
*"o'zini o'chiruvchi integratsiya"* bo'lib yuzaga chiqardi. `connection` va `actor`
uchun ham aynan shunday.

Uzunlik chegarasi **ataylab xom** qiymatda qoladi: avval kesish uzun identifikatorni
bo'shliq bilan to'ldirib chegaradan o'tishga yo'l berardi.

### §146.3. **Ikki jadval bir qadamda turishi kerak** — qayd etildi, o'zgartirilmadi

`RUN_TO_LEDGER` kalitlari `agent_loop.TERMINAL` ga **aniq teng bo'lishi shart**. Bugun
teng. Bu **tekshirildi, chunki yo'q kalit tirik nuqson ko'rinardi va u emas**: agar agent
loop terminal status qo'shsa-yu, xarita qo'shmasa, o'sha run uchun `queued` ledger qatori
**abadiy `queued`** qolardi — va `queued` — `_claim` **yagona** takrorlanadigan deb
hisoblaydigan status, ya'ni cooldown o'tgach lidga **yana yozilardi**.

Xuddi shu shakl `LIMITS` va API so'rov modeli `ReengagementPolicy` o'rtasida ham bor:
model **yettala chegarani ikkinchi marta** e'lon qiladi. O'lchandi — bugun **hammasi
mos**.

### §146.4. Ledger **to'rt** holat e'lon qiladi, **sakkiztasini** yozadi

`LEDGER_*` — `queued`, `settled`, `exhausted`, `failed`. `RUN_TO_LEDGER` esa `cancelled`,
`escalated`, `needs_input`, `uncertain` ni ham yozadi. Bu **ataylab**: izoh *"faqat
`queued` takrorlanadi, qolgani — inson qarori"* deydi, va takrorlanish tekshiruvi
`== LEDGER_QUEUED` ni sinaydi, shuning uchun qo'shimcha qiymatlar **to'g'ri** ishlanadi.
Qayd etildi — keyingi o'quvchi konstantalarni **yopiq to'plamga** "tuzatib" xaritani
buzmasligi uchun.

### §146.5. Revert matritsasi — **27 tadan 3 tasi yashil**

| Guruh | Natija |
|---|---|
| F1–F3, F5–F21, F23–F27 (qo'riqchilar, yettala chegara, `_bounded`, `POLICY_ID_RE`, ledger clamp, `_claim` shartlari, xarita, freeze, authority) | **QIZIL** |
| **F4**, **F22** | **YASHIL** — *halol, ortiqcha* |

**F4:** `_identifier` dagi `not value` ni **quyidagi bo'shliq tekshiruvi** allaqachon
qamraydi (`''` ikkalasida ham yiqiladi). **F22:** `sync_ledger` ning UPDATE idagi
`AND status=?` ni **yuqoridagi SELECT** (`l.status=?`) allaqachon qamraydi — ya'ni
qator umuman tanlanmaydi. Ikkalasi ham **kuchliroq tekshiruv tomonidan yutilgan**;
xulq bilan qadalmadi, **aniq qator bilan** qadaldi va sababi izohda.

Tuzatishdan keyin **27/27 qizil**, `PATTERN-MISSING` **0**, bayt-darajali tiklash
**True**.

### §146.6. Qo'shilgan sinovlar (35 → 84)

`ReengagementBoundaryTests` — mavjud to'plam **xulq**ni tekshiradi, bu sinflar
**qiymatni** qadaydi:

- `test_a_blank_identifier_is_refused`
- `test_the_identifier_length_bound_is_on_the_raw_value`
- `test_a_blank_field_is_refused_through_configure`
- `test_the_limit_table_is_exact`
- `test_every_limit_is_walked_at_both_ends`
- `test_the_policy_id_ceiling_is_sixty_four`
- `test_the_ledger_clamp_is_five_hundred`
- `test_the_run_to_ledger_map_mirrors_the_agent_loop_terminal_set`
- `test_only_queued_is_repeatable`
- `test_the_attempt_ceiling_is_exhausted_not_repeatable`
- `test_the_cooldown_is_inclusive_at_its_boundary`
- `test_sync_ledger_never_rewrites_a_closed_row`
- `test_the_run_input_bounds_are_pinned`
- `test_the_redundant_guards_are_pinned_by_their_lines`

Probe: **1431 → 1555** xossa.

### §146.7. **O'zim qilgan xatolar**

1. **Ikki o'lchovni bir faylda parallel yurgizdim.** Probe va revert matritsasi
   **bir vaqtda** `reengagement.py` ni o'qidi/yozdi — matritsa mutatsiya qilgan faylni
   probe o'qib, **12 yolg'on yiqilish** ko'rsatdi. Ular navbatma-navbat yurishi kerak;
   bu sof harness xatosi, kod haqida hech nima demaydi.
2. **`tick` jadvalni ham suradi.** Har safar 301 sekund qo'shib, "har siklda" deb
   o'yladim — aslida `next_due` `interval_seconds` ga suriladi, shuning uchun **har
   ikkinchi** tick ishlaydi. `max_attempts=10` da 7 ta urinish oldim va buni nuqson deb
   o'qishim mumkin edi. `interval_seconds` va cooldown ikkalasidan ham oshib borish
   kerak.
3. **Cooldown chegarasini `tick` orqali o'lchadim** — jadval chegarani yashirdi.
   `_claim` ni **to'g'ridan-to'g'ri** chaqirish kerak edi.
4. **`ledger_all()[0]`** tartibsiz qatorni oldi, va bazani subtestlar orasida
   tozalamagani uchun keyingi subtest **oldingisining qatorini** o'lchadi.
5. **`policy()` `NotFound` ko'taradi**, `None` qaytarmaydi — `assertIsNone` o'rniga
   `assertRaises` kerak edi.

### §146.8. Saboqlar

1. **Xabar aytgan shartni tekshir.** `"is required"` — bu **bo'sh emaslik** sharti; xom
   qiymatni tekshirib, kesilganini qaytarish boshqa miqdorni o'lchaydi. Bu §144
   (`bank_account`) va §142 bilan **bir oila**.
2. **Tozalash natijani bo'shatib qo'ysa, xato keyinroq va tushunarsiz chiqadi.**
   Bo'sh agent *"rad etilgan konfiguratsiya"* emas, *"o'zini o'chirgan sikl"* bo'ldi.
3. **Ikki modulda yozilgan bir faktni bog'la.** `RUN_TO_LEDGER` ↔ `agent_loop.TERMINAL`
   va `LIMITS` ↔ API modeli — ikkalasi ham bugun mos, lekin ularni **hech narsa
   bog'lamaydi**; tenglikni **sinov** qil, izoh emas.
4. **O'lchovni parallel yurgizma.** Bir faylni mutatsiya qiluvchi matritsa bilan o'sha
   faylni o'qiydigan probe **bir vaqtda** ishlasa, natija **ma'nosiz** — va u yolg'on
   nuqsonlar chiqaradi.
5. **Ko'p martalik sikl jadvalni ham suradi.** "Har tick" deb o'ylash o'rniga, siklning
   **o'zi surgan** holatni hisobga ol.
6. **Ortiqcha qo'riqchi — nuqson emas, lekin qadalishi shart.** Uni **aniq qator** bilan
   qada va sababini yoz.


## §147. Fazza qirq ikkinchi — supervisor va oversight, **o'n birga surilgan shift** va **uchta o'lik konstanta**

### §147.1. Sirt — `supervisor.py` (533) + `oversight.py` (300)

`supervisor` — menejer savolini **qaysi bo'lim agenti** javob berishini hal qiladigan
router. `oversight` — inson agentning nima qilganini **ko'radigan** oyna. Ya'ni ularning
chegaralari: *kim nimaga javob beradi* va *operator nimani ko'ra oladi*.

| # | Chegara | Qiymat |
|---|---|---|
| 1 | `MAX_SECTIONS` | 20 |
| 2 | `MAX_QUESTION` | 2 000 |
| 3 | `MAX_KEYWORDS` | 20 |
| 4 | `MAX_KEYWORD_LENGTH` | 60 |
| 5 | `MAX_HOPS_CEILING` | 3 |
| 6 | `MAX_STEPS_CEILING` | 12 |
| 7 | `DEFAULT_MAX_HOPS` / `DEFAULT_MAX_STEPS` | 1 / 6 |
| 8 | `NAME_RE` / `AGENT_RE` | 1..128 |
| 9 | `request_key` | **245** (256 emas) |
| 10 | `history` / ledger `limit` | 1..500 |
| 11 | `MAX_EVENTS` | 100 |
| 12 | `MAX_WINDOW_SECONDS` / `DEFAULT_WINDOW_SECONDS` | 90 / 7 kun |
| 13 | `VIEWS` | 3 ko'rinish |

### §147.2. Nuqson A — shift **boshqa satrni** chegaralagan, **o'n birga**

`route` `len(request_key) > 256` ni tekshirardi, keyin run kalitini
`f'supervisor:{request_key}'` deb yasardi — `AgentLoop.create` esa **kalitni** 256 bilan
chegaralaydi. O'lchandi (qavsda hosil bo'lgan kalit):

| `request_key` | Natija |
|---|---|
| 244 | **ok** (kalit 255) |
| 245 | **ok** (kalit 256) |
| **246** | **`ValueError: Invalid run identity`** (kalit 257) |
| 256 | **`ValueError: Invalid run identity`** (kalit 267) |

Ya'ni **amaldagi shift 245 edi, xabar esa 256 ni va'da qilardi** — va 246 dan yuqori har
bir uzunlik `request_key` haqida emas, **run identifikatori** haqida xabar bilan rad
etilardi. Endi `RUN_KEY_PREFIX` va `MAX_REQUEST_KEY = 256 - len(RUN_KEY_PREFIX)` —
**bitta ifoda**, va kalit **o'sha konstantadan** yasaladi.

### §147.3. Nuqson B — **uchta o'lik konstanta**, uch xil muomala

| Konstantа | Nima qiladi | Qaror |
|---|---|---|
| `supervisor.MAX_SECTIONS = 20` | **hech nima** | **ulandi** |
| `oversight.MAX_RUNS = 100` | **hech nima** | **olib tashlandi** |
| `oversight.VIEWS = (...)` | **hech nima** | **ulandi** |

Uchtasi ham **faqat o'z e'lonida** uchraydi. Lekin ular bir xil holat emas, shuning uchun
bir xil muomala ham qilinmadi:

- **`MAX_SECTIONS` — haqiqiy chegara, hech qachon ulanmagan.** Modulning o'z docstring'i
  *"a bounded router"* deydi, yonidagi `MAX_KEYWORDS` **amalda**, va qo'llash nuqtasi
  bir xil. O'lchandi: **yigirma bitta** bo'lim e'lon qilinib, **saqlandi**. Endi shiftdan
  oshgan **yangi** bo'lim rad etiladi, **mavjudini qayta e'lon qilish doim ruxsat** —
  ya'ni shiftdan ko'p bo'limga ega tenant **ishlashda davom etadi**.
- **`MAX_RUNS` — mavjud bo'lmagan narsani chegaralaydi.** `activity` runlarni status
  bo'yicha `GROUP BY` bilan **agregatlaydi**, ya'ni cheklanadigan **run ro'yxati yo'q**.
  **Olib tashlandi**, qo'llash nuqtasi **o'ylab topilmadi**.
- **`VIEWS` — chegara emas, o'quvchisi yo'q e'lon qilingan to'plam.** Yopiq to'plam faqat
  `_dispatch` ning if-zanjirida yashardi. Endi u **qo'riqchi** — e'lon **qaror qiladi**.

### §147.4. Nuqson C — qo'riqchi **bir shaklni** tekshirib, **boshqasini** qaytargan

`oversight._text` **kesilgan** qiymatni bo'shligiga qarardi va **xom**ini qaytarardi; uning
yagona chaqiruvchisi esa **xomini** regex bilan tekshirardi. O'lchandi:

| Chaqiruv | Natija |
|---|---|
| `oversight._known(' sales ')` | **`ValueError: Invalid agent id`** |
| `supervisor._name(' sales ')` | **`'sales'`** |

Ikki modulning qo'shilgan bo'shliqli identifikator haqida **kelishmasligi** — ikkala
javobning o'zidan ham yomonroq. Endi `_text` **o'zi tekshirgan qiymatni qaytaradi**, va
`_known` undan foydalanadi.

### §147.5. Bir ortiqcha qo'riqchi — qayd etildi, o'zgartirilmadi

`_keywords` har bir kalitni **xom** elementda chegaralaydi
(`len(item) > MAX_KEYWORD_LENGTH`), keyin **kesilganini** saqlaydi. Bu identifikator
qo'riqchilaridagi **aynan o'sha assimetriya**, lekin **fail-closed** yo'nalishda — shuning
uchun "tuzatilmadi", **aniq qator bilan qadaldi**.

### §147.6. Revert matritsasi — **30 tadan 22 tasi yashil**

| Guruh | Natija |
|---|---|
| G11–G13, G16, G17, G24, G28, G29, G30 (hop/step shiftlari, kalit so'z soni, hop cap, `VIEWS`, `AGENT_RE`, probe, `_bounded`) | **QIZIL** |
| G1–G10, G14, G15, G18–G23, G25–G27 | **YASHIL** |

Tuzatishdan keyin **29/30 qizil**; `PATTERN-MISSING` **0**, bayt-darajali tiklash **True**.

**G1 (tuzatishni qaytarish), G4 (bo'lim shifti) va G26 (`_text`) — uchtasi ham yashil
edi**, ya'ni tuzatishlarning o'zi **qayta kiritilishi mumkin** edi.

**G2 — sinov **kim** rad etganini ajratmadi.** `MAX_REQUEST_KEY` ni 256 ga qaytargan
mutatsiya **yashil** qoldi, chunki 246 belgili kalitni **AgentLoop ham** `ValueError`
bilan rad etadi — ya'ni sinov faqat **istisno turini** tekshirar va tsiklning rad etishi
o'lchanayotgan qo'riqchi o'rniga o'tib ketardi. Endi sinov **xabarni** ham tekshiradi
(`'request_key' in str(...)` va `'run identity' not in ...`). Bu §143 dagi **"ikki yaqin
chegarani ajratadigan kirishni tanla"** darsining yangi ko'rinishi: bu yerda ikki chegara
**ikki modulda**.

**G15 va G23 — halol yashil.** `NAME_RE` va `AGENT_RE` ning `{0,127}` i ni **o'z
modulidagi uzunlik tekshiruvi** qamraydi: regex ko'pi bilan 129 belgiga yo'l qo'yadi, 129
esa regexga yetib bormasdan rad etiladi. Shuning uchun `{0,128}` ga kengaytirish **hech
narsani o'zgartirmaydi**. Ikkalasi ham **aniq qator bilan** qadaldi va sababi izohda.

### §147.7. Qo'shilgan sinovlar (60 → 132; oversight 38 → 86)

`SupervisorBoundaryTests`:

- `test_the_limit_constants_are_pinned`
- `test_the_name_ceiling_is_one_hundred_and_twenty_eight`
- `test_the_keyword_caps_are_pinned`
- `test_the_bounded_helper_refuses_a_non_int`
- `test_the_request_key_ceiling_leaves_room_for_the_run_prefix`
- `test_the_request_key_ceiling_holds_at_the_boundary`
- `test_the_section_ceiling_is_enforced`
- `test_the_hop_cap_holds_at_its_boundary`
- `test_the_route_bounds_are_walked`
- `test_the_history_clamp_is_five_hundred`
- `test_the_keyword_precedence_is_longest_then_alphabetical`
- `test_the_redundant_regex_bounds_are_pinned_by_their_lines`

`OversightBoundaryTests`:

- `test_the_limit_constants_are_pinned`
- `test_the_window_and_limit_bounds_are_walked`
- `test_the_bounded_helper_refuses_a_non_int`
- `test_the_view_set_is_the_guard`
- `test_max_runs_is_gone`
- `test_text_returns_the_value_it_validated`
- `test_the_agent_id_ceiling_is_one_hundred_and_twenty_eight`
- `test_the_activity_probe_reads_one_past_the_limit`
- `test_the_redundant_regex_bound_is_pinned_by_its_line`
- `test_the_tool_surface_is_the_declared_one`

Probe: **1555 → 1683** xossa.

### §147.8. **O'zim qilgan xatolar**

1. **`_depth` ni hisobga olmadim.** U **nishon agent bo'yicha** sanaydi, bo'lim bo'yicha
   emas — shuning uchun oldingi yozuvlarning hammasi keyingi `route` ni **ikkinchi hop**
   qilib qo'ydi va `max_hops=1` uni rad etdi. **Yetti yozuv** "Forbidden" bo'lib chiqdi va
   men ularni nuqson deb o'qishim mumkin edi. Har bir yozuv oldidan ledger tozalandi.
2. **`setUp` `seed()` ni chaqirmasligini unutdim** — bo'lim shifti sinovida 20 emas, 17
   bo'lim bo'lgan edi.
3. **`history(TENANT, 0)` bir qator qaytaradi deb o'yladim** — bo'sh jadvalda u **nol**
   qaytaradi, chunki clamp 0 ni 1 ga ko'taradi, keyin `LIMIT 1` bo'sh jadvaldan hech nima
   olmaydi.
4. **`type(_dispatch(...)).__name__ == 'ok'`** deb yozdim — `_dispatch` **dict** qaytaradi.
5. **`_text` va `_known` ni import qilishni unutdim** — oversight sinovlari `NameError`
   bilan yiqildi.
6. **Sinov faqat istisno turini tekshirdi** (G2) — ikkala modul ham `ValueError`
   ko'targani uchun mutatsiya yashil qoldi. **Kim** rad etganini tekshirish kerak edi.

### §147.9. Saboqlar

1. **Shiftni **hosil qilinadigan** qiymatda o'lcha.** `request_key` 256 gacha ruxsat
   berilardi, lekin kalit unga **11 belgi** qo'shib yasaladi — ya'ni amaldagi shift 245.
   Ikkala sonni **bitta ifodaga** bog'la.
2. **O'lik konstanta — uch xil holat, uch xil javob.** Ulanishi mumkin bo'lgan haqiqiy
   chegara → **ula**; mavjud bo'lmagan narsani chegaralovchi → **olib tashla va qayd et**;
   o'quvchisiz e'lon qilingan to'plam → **qo'riqchi qilib ula**. Bittasini tanlab,
   qolganiga qo'llash — mexanik xato.
3. **Bir xil tushunchani ikki modul har xil talqin qilmasin.** `supervisor._name` va
   `oversight._text` — ikkalasi ham "identifikator"; biri kesadi, ikkinchisi rad etardi.
   **Bir xil javobni** tanla va **sinov bilan** bog'la.
4. **Agregat — ro'yxat emas.** `GROUP BY` qiladigan joyda "nechta qator" chegarasi
   **qo'llanadigan narsa emas**; uni o'ylab topish o'rniga **yo'qligini** yoz.
5. **Umumiy holat yozuvlar orasida oqib ketadi** — `_depth` kabi **jamlanuvchi** hisob
   bo'lsa, har bir yozuvdan oldin uni **tozala**, aks holda keyingi yozuv oldingisining
   natijasini o'lchaydi (§146 dagi fixture oqishi bilan bir oila).
6. **Ikki modul bir xil xatoni ko'tarsa, sinov ularni ajratmaydi.** 246 belgili
   `request_key` ni **ikkalasi ham** `ValueError` bilan rad etadi, shuning uchun istisno
   turi yetarli emas — **xabar** (yoki **qaysi qatlam** rad etgani) tekshirilsin.

> **Eslatma (2026-09-21).** Bu ro'yxatda 7, 8 va 9-bandlar 1, 2 va 4-bandning
> takrori edi (bir xil dars, boshqa so'zlar bilan). Ular olib tashlandi.
> Takrorlanishning oldini olish uchun darslar endi `boundary-audit` skill'ining
> `references/` fayllarida **mavzu bo'yicha** saqlanadi va yangi dars qo'shishdan
> oldin o'sha yerda qidiriladi.

## §148. Fazza qirq uchinchi — OAuth chegaralari va **beshta rad etish bitta xabar bo'lib qolgan**

**Sirt:** `platform_runtime/oauth.py`, 378 satr, 49 test. Skan paytida e'tibor
tortdi: modulda **birorta ham nomlangan konstanta yo'q**, lekin **to'qqizta**
sonli chegara bor — hammasi **inline literal**.

### §148.1. Chegaralar, va ularni kim tekshirardi

| Chegara | Qiymat | Joy |
|---|---|---|
| `bounded` standart | 256 | umumiy maydon darvozasi |
| talab qilinadigan scope soni | 40 | provayder nechta e'lon qila oladi |
| berilgan scope qatori | 10 000 | provayderning o'z javobi |
| token umri | 60 .. 86 400 | `expires_in` |
| PKCE verifier | 43 .. 128 | RFC 7636 |
| identifikator | 1 .. 128 | ulanish va provayder nomlari |
| authorization code | 4 096 | callback parametri |
| access / refresh token | 16 000 | provayder bergan credential |

### §148.2. O'lchov — revert matritsasi, **9/9 yashil**

Yangi asbob: `scripts/probes/revert_matrix.py` (qayta ishlatiladigan; faza skripti
`scripts/probes/audit_oauth_bounds.py`). Nazorat yashil, keyin **to'qqizta mutatsiya
hammasi yashil**:

```
CONTROL                GREEN   OK
bounded default 256    GREEN   9.6s  OK
required scopes 40     GREEN   8.6s  OK
granted scope 10000    GREEN   9.1s  OK
expiry ceiling 86400   GREEN   7.5s  OK
expiry floor 60        GREEN   7.9s  OK
PKCE floor 43          GREEN   9.5s  OK
identifier ceiling 128 GREEN   9.3s  OK
authorization code 4096 GREEN  9.8s  OK
access token 16000     GREEN   9.4s  OK
```

**Qirq to'qqizta test butun oqimni uchidan-uchiga yurardi va bittasi ham
chegarani o'lchamagan edi.** Oqim testi oqim ishlashini isbotlaydi; u shift
**256** ekanini **257** dan ajratmaydi.

### §148.3. Haqiqiy nuqson — **beshta aniq rad etish bitta umumiy xabarga aylanardi**

Chegaralarni qadash uchun sinov yozayotganda ma'lum bo'ldi: `complete()` da
`_tokens()` **keng `except Exception`** ichida chaqiriladi, va u **hamma narsani**
`OAuthError('Authorization outcome unavailable; authorize again')` ga o'raydi.
`OAuthError` — `RuntimeError` avlodi, ya'ni **o'z xatosi ham o'sha handler'ga
tushadi**.

O'lchandi (tuzatishdan **oldin**), beshta **butunlay boshqa** rad etish:

| Provayder javobi | Operator ko'rgan xabar |
|---|---|
| `expires_in = 59` | Authorization outcome **unavailable** |
| `expires_in = 86401` | Authorization outcome **unavailable** |
| `expires_in = "3600"` | Authorization outcome **unavailable** |
| `scope` = 10 001 belgi | Authorization outcome **unavailable** |
| `access_token` = 16 001 belgi | Authorization outcome **unavailable** |

Ya'ni **javob to'liq o'qilgan va tushunilgan**, lekin operator **tarmoq
nosozligini** qidirishga yuboriladi, va auditga `uncertain` yoziladi. Bu
skill'ning "qo'riqchi o'z `try`'ining keng handler'i tomonidan ushlanadi"
darsining aynan o'zi — to'rt transportda topilgan naqshning uchinchi nusxasi.

**Tuzatish ikki qavatli:**

1. `_tokens` ichida `credential()` yordamchisi — `bounded` ning `ValueError` ini
   **`OAuthError`** ga o'giradi, ya'ni metodning shartnomasi bir xil bo'ladi
   (boshqa hamma chegara allaqachon `OAuthError` ko'tarardi).
2. `complete`/`access` da **`except (OAuthError, Forbidden)`** shoxi **keng
   handler'dan oldin** — sabab saqlanadi. `Forbidden` (`PermissionError`) bu
   yerda **provayder tomonidagi** rad etish, shuning uchun u chaqiruvchining
   shartnomasiga (`OAuthError`) o'tkaziladi, lekin **matni saqlanadi**.

**Holat mashinasi o'zgarmadi:** `_failure(..., 'uncertain')` va
`_cleanup_issued(...)` baribir ishlaydi, chunki **ishlatib bo'lmaydigan
credential baribir bekor qilinishi kerak**. Faqat **sabab** tiklandi.

Tuzatishdan keyin: **5 xil xabar** (7 rad etishdan; uchta expiry varianti bitta
to'g'ri xabarni bo'lishadi).

### §148.4. Revert matritsasi — **9/9 qizil**

```
bounded default 256    RED  18.3s
required scopes 40     RED  19.8s
granted scope 10000    RED  19.4s
expiry ceiling 86400   RED  18.2s
expiry floor 60        RED  18.5s
PKCE floor 43          RED  20.0s
identifier ceiling 128 RED  19.4s
authorization code 4096 RED 19.8s
token ceiling 16000    RED  19.5s
restore verified: YES
```

### §148.5. Qo'shilgan sinov va probe

- `test_oauth.py`: yangi `DeclaredBoundTests` — **8 sinov**, `test_oauth.py`
  49 → **57**. Har biri literalni **aniq satr** bilan qadaydi (oxiridagi `\n`
  bilan: `b'maximum=256'` ni `maximum=2560` ham qanoatlantiradi) **va** chegarani
  **ikki tomondan** yuradi. Sinf **`OAuthTests` dan meros olmaydi** — pastga
  qarang.
- `scripts/probes/probe_oauth_boundaries.py` — **48 xossa, 48 pass**, 4 bo'lim: darvozalar,
  provayder javobi chegaralari, **har rad etish o'z sababini aytadi**, va rad
  etilgan exchange baribir **fence qilinadi**.

### §148.6. O'zim qilgan xatolar

1. **Probe'da darvozalarga son berdim** (`bounded(256)`) — ular **satr** oladi.
   Natijada 6 ta "qizil" chiqdi, ular **mening** xatolarim edi, modulning emas.
   Tuzatildi: uzunlik yuritiladi (`bounded('x' * n)`).
2. **Matritsani SIGTERM bilan o'ldirdim va bitta mutatsiya faylda qoldi**
   (`PKCE floor 43` → `42`). Keyingi yurish **mutatsiyani baseline deb o'qidi**
   va nazorat qizil bo'ldi — bu aynan skill yozgan tuzoq. Ikkalasi ham tuzatildi:
   `revert_matrix.py` endi **sidecar** saqlaydi, uzilgan yurishni **aniqlaydi**,
   va `atexit` + `SIGINT`/`SIGTERM` bilan tiklaydi. Mexanizm **sinovdan
   o'tkazildi** (soxta uzilish yaratildi → aniqlandi → tiklandi).

### §148.7. Saboqlar

1. **"Birorta konstanta yo'q" degani "chegara yo'q" degani emas.** To'qqizta
   inline literal — nomlangan konstanta bo'lmagani uchun **skanga tushmadi**;
   ularni qo'lda sanash kerak bo'ldi.
2. **Oqim testi chegarani o'lchamaydi.** 49 test to'liq oqimni yurardi va
   to'qqizta chegaradan **bittasini ham** o'lchamagan edi.
3. **Keng `except` modulning o'z xatosini ham yutadi.** `OAuthError`
   `RuntimeError` avlodi, shuning uchun "faqat kutilmaganini o'rayman" degan
   niyat bajarilmaydi — **o'z turini keng handler'dan oldin** qayta ko'tarish
   kerak.
4. **Uzilgan matritsa keyingi yurishning baseline'i bo'lib qoladi.** Buni
   **aniqlaydigan** mexanizm kerak, chunki `atexit` signal bilan o'ldirilganda
   ishlamaydi.
5. **Meros — faqat fixture o'zgarganda.** Bu sinfni dastlab `OAuthTests` dan
   meros qildirdim, natijada **43 ta oqim sinovi behuda ikkinchi marta** yurdi
   (49 → 101 test, +8s), chunki sinf **fixture'ni o'zgartirmaydi**. Repo'dagi
   naqsh (`SupervisorBoundaryTests`, `SheetsBoundaryTests`) **fixture'ni
   o'zgartiradi**, shuning uchun meros o'sha yerda oqlanadi. Meros — qayta
   ishlatish emas, **qayta yuritish**.

### §148.8. Yakuniy baseline — **o'lchandi, keyin yozildi**

Faza tugagach butun to'plam **qayta yurildi** (boshqariladigan venv,
`cryptography` bilan):

```
Ran 2792 tests in 413.324s

FAILED (failures=1, errors=11, skipped=1)
```

| Ko'rsatkich | Qiymat |
|---|---|
| Testlar | **2784 → 2792** = **+8** (`DeclaredBoundTests`), boshqa o'zgarish yo'q |
| Imzo | `failures=1, errors=11, skipped=1` — **hujjatlashtirilgan baseline bilan aynan** |
| Vaqt | 413 s (oldingi A/B: 443 s) |
| Yagona `failure` | Windows-only `test_all_files_private` — **oldindan mavjud** |
| 11 `error` | POSIX-only xavfsizlik kontrakti — **oldindan mavjud** |

`test_oauth.py` alohida: **57 test, 9.0 s, OK**. `oauth.py` diff: **+53 satr**
(tuzatish), `test_oauth.py` **+182 satr** (8 qadalgan sinov).

**Muhim:** baseline **kod o'zgarishidan keyin** o'lchandi, ya'ni "imzo
o'zgarmadi" — **da'vo emas, o'lchov**.

---

## §149. Fazza qirq to'rtinchi — `tools.py` chegaralari va **ikki sabab bitta xabar**

**Sirt:** `platform_runtime/tools.py`, **303 satr**, 18 test (`test_adapters.py`).
Bu — **har bir tool argumenti o'tadigan darvoza** va pack'ga qaysi nomlar
mavjudligini aytadigan modul.

### §149.1. Nega aynan bu modul tanlandi

Skan **mexanik** edi, taassurot emas: modulda **13 ta katta sonli literal** va
**bitta ham nomlangan konstanta yo'q**. Ya'ni chegaralarni konstanta bo'yicha
qidirish ularni **umuman ko'rmaydi** — qo'lda sanash kerak bo'ldi. Bu §148 dagi
`oauth.py` bilan **bir xil sabab**, lekin u yerda 9 ta edi.

### §149.2. Chegaralar, va ularni kim tekshirardi

| Chegara | Qiymat | Joy |
|---|---|---|
| sxema chuqurligi | 64 daraja | `validate_schema` rekursiya shifti |
| argument obyekti | 20 000 **bayt** | `validate_schema` object shoxi |
| massiv uzunligi | 0 .. 100 | `validate_schema` array sukuti |
| qator uzunligi | 0 .. 4 000 | `validate_schema` string sukuti |
| butun son oralig'i | ±10**12 | `validate_schema` integer sukuti |
| `string()` yordamchisi | 4 000 | **har bir** tool maydonining sukuti |
| provayder javobi | 1 000 000 bayt | `post_json` o'qish shifti |
| provayder timeout | 25 s | `post_json` sukuti |
| xotira qidiruvi | 10 qator | `memory_search` LIMIT |
| yozuvlar ro'yxati | 50 qator | `records_list` LIMIT |
| credential havolasi | `[A-Z][A-Z0-9_]*` | `secret` nom shakli |
| tool risk darajasi | 4 qiymat | `Registry.add` |

### §149.3. O'lchov — birinchi matritsa, **15 mutatsiyadan 10 tasi YASHIL**

```
CONTROL                GREEN           OK
argument object 20000 bytes RED          FAILED (failures=1)
array maxItems default 100 RED           FAILED (failures=1)
array minItems default 0 GREEN           OK
string maxLength default 4000 RED        FAILED (failures=1)
string minLength default 0 GREEN         OK
integer maximum default 10**12 RED       FAILED (failures=1)
integer minimum default -10**12 RED      FAILED (failures=1)
string() helper default 4000 GREEN       OK
provider response read cap GREEN         OK
provider response ceiling GREEN          OK
provider timeout 25s   GREEN             OK
memory search LIMIT 10 GREEN             OK
records list LIMIT 50  GREEN             OK
credential name shape  GREEN             OK
tool risk level set    GREEN             OK
```

Ya'ni **besh chegara** qadalgan edi, **o'ntasi yo'q**. Eng qimmati —
`string() yordamchisi`: bu **har bir tool maydonining** sukut chegarasi, ya'ni uni
kengaytirish **hamma** maydonni jimgina kengaytiradi, va **hech narsa sezmadi**.

**Muhim o'lchov:** `Registry.add` ning risk tekshiruvi **ham** yashil chiqdi —
`test_registration_contract.py` faqat *takroriy nom* shartnomasini o'lchaydi,
risk to'plamini emas.

### §149.4. Haqiqiy nuqson A — **ikki sabab, bitta xabar**

`Registry.add` da bitta shart **ikki xil sababni** bitta xabar bilan rad etardi:

| Holat | Tuzatishdan **oldin** | Tuzatishdan **keyin** |
|---|---|---|
| nom takrorlangan | `Invalid tool registration` | `Tool already registered: x.y` |
| risk noma'lum | `Invalid tool registration` | `Unknown tool risk level: bogus` |
| ikkalasi birga | `Invalid tool registration` | (risk nomi aytiladi) |

Bu — §148 dagi "beshta rad etish bitta xabar" naqshining **aynan o'zi**, bir
qatlam yuqorida. Va eng muhimi: **bu xabarni shu repo o'zi allaqachon shikoyat
qilgan** — `register_once` ning docstring'i (P15 da yozilgan) aynan shunday deb
yozadi: *"a second call must be a no-op rather than a duplicate-name error whose
message says nothing about the cause"*. Ya'ni **da'vo to'g'ri edi va kod hali ham
uni bajarayotgan edi**; `register_once` xatoni **chetlab o'tgan**, uni **nomlamagan**.

**Tuzatish:** rad etish **o'zgarmadi** (baribir `ValueError`), faqat **sabab**
nomlandi. Risk **birinchi** tekshiriladi, chunki u qo'shilayotgan tool'ning
xossasi, takroriylik esa registrning **holati**.

### §149.5. Haqiqiy nuqson B — **qo'riqchi rad etish o'rniga yiqilardi**

`validate_schema` **operator konfiguratsiyasi** bo'lgan sxema bo'ylab har
daraja uchun **bir marta rekursiya** qiladi, ya'ni sxema chuqurligi bu modul
nazorat qilmaydigan kattalik.

**O'lchandi (tuzatishdan oldin):**

| Sxema chuqurligi | Natija |
|---|---|
| 200 / 500 / 900 | qabul qilindi |
| **1500** | **`RecursionError`** — `ValueError` shartnomasi emas |

**Va bu yetib boriladi, nazariy emas:** `mcp.call` **chaqiruvchi** argumentlarini
**operator** e'lon qilgan sxema bo'yicha tekshiradi, `arguments_json` esa
**12 000 belgi** oladi — 1500 daraja uchun ~10 500 belgi yetarli. Ya'ni
`RecursionError` **chaqiruvchi tomonidan** chaqirilishi mumkin edi, va u
`ValueError` emas, shuning uchun hech bir chaqiruvchi uni **rad etish** deb
tushunmaydi.

**Tuzatish:** `MAX_SCHEMA_DEPTH = 64` — qo'riqchi **rad etadi**, yiqilmaydi.
**Raqam o'lchandi, o'ylab topilmadi:** 88 tool'ning eng chuqur sxemasi —
**3 daraja**, ya'ni shift 21× keng.

**Ikkinchi shift ham o'lchandi va RAD ETILDI (nuqson emas):** `encode()` **butun**
qiymat bo'yicha, qo'riqchi rekursiyasidan **oldin** chaqiriladi, JSON enkoderi esa
o'zi rekursiv — demak ikkinchi shift bor. Bisection bilan o'lchandi:
**enkoder 2998 darajada ishlaydi, 2999 da yiqiladi**; 12 000 belgi esa ko'pi bilan
**1999** daraja tashiydi. Ya'ni **1999 < 2998** — bu shift **yetib borilmaydi**, va
uni "tuzatish" kerak emas. Probe buni **har yurishda qayta o'lchaydi**.

### §149.6. Qaytarish matritsasi — **17/17 qizil**

Konstantalar nomlangach mutatsiya nishonlari ham nom bo'ldi (bir son — bir joy).
Ikki rejim **konstanta emas**, balki shu fazada tuzatilgan ikki nuqson: qadalgan
bo'lmasa, tuzatish **jimgina qaytarilishi** mumkin edi.

```
schema nesting 64      RED  22.7s  FAILED (failures=1)
depth guard removed    RED  22.6s  FAILED (failures=1)
argument object 20000 bytes RED  22.6s  FAILED (failures=2)
array maxItems default 100 RED  22.4s  FAILED (failures=2)
array minItems default 0 RED    22.6s  FAILED (errors=1)
string maxLength default 4000 RED  22.6s  FAILED (failures=3)
string minLength default 0 RED  22.9s  FAILED (errors=1)
integer bound 10**12   RED  22.6s  FAILED (failures=3)
string() helper default RED  22.7s  FAILED (failures=1)
provider response ceiling RED  22.4s  FAILED (failures=1)
provider timeout 25s   RED  22.5s  FAILED (failures=1)
memory search LIMIT 10 RED  22.6s  FAILED (failures=1)
records list LIMIT 50  RED  22.6s  FAILED (failures=1)
credential name shape  RED  23.2s  FAILED (failures=1)
tool risk level set    RED  23.6s  FAILED (failures=1)
duplicate name not named RED  22.4s  FAILED (failures=1)
risk refusal not named RED  22.5s  FAILED (failures=1)
restore verified: YES
```

**Asbob yaxshilandi:** `scripts/probes/revert_matrix.py` endi **bir nechta** test
faylini bitta yurishda o'lchaydi (dotted spec), chunki bu chegaralar bir necha
faylda qadalgan. Bitta fayl bilan chegaralansak, **qo'shni fayldagi** sinov
qadaydigan chegara **yolg'on YASHIL** chiqardi — bu asbobning xatosi bo'lardi,
kodning emas. Eski `*.py` rejimi o'zgarmadi.

### §149.7. Qo'shilgan sinov va probe

- `test_adapters.py`: yangi `DeclaredBoundTests` — **11 sinov**, 18 → **29**.
  Har biri **literalni** qadaydi (konstantani moduldan **qayta o'qimaydi** — §142.5
  da oltita chegara aynan shu sababdan yashil chiqqan edi) **va** chegarani
  **ikki tomondan** yuradi.
- `scripts/probes/probe_tools_boundaries.py` — **66 xossa, 66 pass**, 6 bo'lim:
  literallar, sxema qabuli, **tuzatilgan ikki nuqson**, transport, o'qish
  shiftlari, credential nom shakli.

### §149.8. O'zim qilgan xatolar (yashirilmadi)

1. **Probe'da argumentlar tartibini almashtirdim** — `validate_schema(value, schema)`
   ni `validate_schema(*nested(n))` bilan chaqirdim, ya'ni `(schema, value)`.
   Natijada "1 daraja qabul qilindi" **FAIL** bo'ldi — modul emas, **mening**
   xatom. O'lchov quroli ham tekshirilishi kerak (§134.9 darsi, yana bir nusxa).
2. **Probe'da `staticmethod` ishlatdim** va `self` bog'lanishini yo'qotdim —
   timeout **None** bo'lib chiqdi. Transport bo'limi qayta yozildi.
3. **`_object_of_bytes` bir baytga xato edi** — `encode` **ixcham** JSON yozadi
   (`{"a":""}` — 9 emas, **8** bayt). Test **o'z shartini assert qilgani** uchun
   darhol ko'rindi.
4. **Bisection predikati boolean qaytargan, lekin `deepest` istisno kutgan edi** —
   `carried` 8000 bo'lib chiqdi. `RecursionError` endi "bajarilmadi" deb sanaladi.

### §149.9. Saboqlar

1. **"Konstanta yo'q" — skan uchun ko'rinmaslik, chegarasizlik emas.** Bu
   takrorlanish: §148 da 9 ta, bu yerda 13 ta literal qo'lda sanaldi.
2. **Modul o'z nuqsonini allaqachon yozib qo'ygan bo'lishi mumkin.** `register_once`
   ning docstring'i xatoni **to'g'ri tasvirlagan**, lekin kod uni **tuzatmagan**.
   Hujjat bilan kod zid bo'lsa — **kod** o'zgaradi (§129, §130, §134 naqshining
   yangi nusxasi).
3. **Qo'riqchi shartnomadan boshqa istisno ko'tarsa, u qo'riqchi emas.**
   `RecursionError` — bu **rad etish** emas, **yiqilish**.
4. **Har bir shiftni "tuzatish" shart emas — qaysi biri yetib borilishini
   o'lchash kerak.** `encode()` ning 2998 shifti **haqiqiy**, lekin 12 000 belgi
   faqat 1999 daraja tashiydi, ya'ni u **o'lik**. Uni tuzatish — o'lchanmagan ish.
5. **Bir faylli matritsa qo'shni fayldagi qadalgan chegarani "yashil" deb
   ko'rsatadi.** Asbob ham o'lchov quroli, va u ham **tekshirilishi** kerak.

### §149.10. Yakuniy baseline — o'lchandi

```
Ran 2803 tests in 270.910s

FAILED (failures=1, errors=11, skipped=1)
```

| Ko'rsatkich | Qiymat |
|---|---|
| Testlar | **2792 → 2803** = **+11** (`DeclaredBoundTests`), boshqa o'zgarish yo'q |
| Imzo | `failures=1, errors=11, skipped=1` — **o'zgarmadi** |
| `tools.py` | 303 → **343 satr**; nomlangan konstanta **0 → 11** |
| Registry | **88** tool (o'zgarmadi), `known_tool_names()` **89** |
| Matritsa | 15 → **17 mutatsiya**, **17/17 qizil**, 0 yashil, 0 o'lchanmagan |
| Probe | **66 xossa, 66 pass** |
| Yagona `failure` / 11 `error` | **oldindan mavjud** (Windows-only / POSIX-only) |

## §150. Fazza qirq beshinchi — `secret_vault.py` chegaralari va **uchta o'lgan chegara**

**Sirt:** `platform_runtime/secret_vault.py`, **99 satr**, 11 test
(`test_secret_vault.py`). Bu — **har bir credential saqlanadigan** modul: OAuth
tokenlari shu yerdan o'tadi, ya'ni bu yerdagi har bir chegara yo "nimani
shifrlash mumkin" yoki "nimani shifrmatn deb qabul qilinadi" degan savolga javob
beradi.

### §150.1. Nega aynan bu modul tanlandi

Skan **mexanik**, taassurot emas: modulda **6 ta katta sonli literal** va **bitta
ham nomlangan konstanta yo'q** — §148 dagi `oauth.py` va §149 dagi `tools.py`
bilan **aynan bir xil sabab**, uchinchi nusxa.

Farqi shundaki, bu modulda **rad etish xabarlarining o'zi ham chegara**: `seal` va
`open` `except Exception` bilan **hamma** sababni bitta matnga aylantiradi. Ya'ni
bu yerda "ikki sabab bitta xabar" naqshi **ataylab** bo'lishi ham mumkin —
o'lchash kerak, taxmin qilish emas.

### §150.2. Chegaralar, va ularni kim tekshirardi

| Chegara | Qiymat | Joy |
|---|---|---|
| kalit halqasi qavati | 1 | `SecretVault.__init__` |
| kalit halqasi shifti | 8 | `SecretVault.__init__` |
| kalit id shakli | `[A-Za-z0-9_-]{1,64}` | halqa tekshiruvi |
| kalit materiali | **aniq** 32 bayt | halqa tekshiruvi |
| takroriy kalit materiali | rad etiladi | halqa tekshiruvi |
| shifrlash konteksti | bo'sh emas, 4096 bayt | `_aad` |
| muhrlangan yuk | 64 000 bayt | `seal` |
| ochilgan yuk | 64 000 bayt | `open` |
| konvert | 90 000 belgi | `open` |
| konvert maydonlari | **aniq** to'rtta, `v == 1` | `open` |
| nonce | **aniq** 12 bayt | `open` |
| kodlangan maydon | 150 000 belgi | `_unb64` |

### §150.3. O'lchov — birinchi matritsa, **13 mutatsiyadan 8 tasi YASHIL**

```
CONTROL                     GREEN    OK
base64 field 150000         GREEN    OK
key ring upper 8            GREEN    OK
key id ceiling 64           GREEN    OK
key material 32 bytes       RED      FAILED (errors=63)
duplicate key material      RED      FAILED (failures=1)
context ceiling 4096        GREEN    OK
context must be non-empty   GREEN    OK
seal payload 64000          RED      FAILED (failures=1)
envelope 90000              GREEN    OK
envelope field set          GREEN    OK
envelope version 1          RED      FAILED (errors=41)
nonce 12 bytes              RED      FAILED (errors=41)
opened payload 64000        GREEN    OK
```

**Sakkizta yashil** — lekin ularning hammasi bir xil turdagi emas. Ular **uch
guruhga** bo'lindi, va guruhni aniqlash uchun **o'lchash** kerak bo'ldi:

| Guruh | Chegaralar | Nima uchun yashil |
|---|---|---|
| **Qadalmagan** | halqa 8, kalit id 64, kontekst 4096, kontekst bo'sh emas, konvert maydonlari | Kod **bajaradi**, lekin **hech bir test qadamaydi** |
| **Qo'riqchi ko'rinmas** | konvert 90 000 | `test_invalid_envelope_shapes` `'x'*90001` ni **sinaydi**, lekin u **boshqa sababdan** rad etiladi |
| **Yetib borilmaydigan** | kodlangan maydon 150 000, ochilgan yuk 64 000 | Bu chegaralarga **hech bir yo'l** yetib bormaydi |

Uchinchi guruh — §149.5 dagi "enkoder 2998" darsining **yangi nusxasi**: yashil
rang "qadalmagan" degani emas, **"o'lchab bo'lmaydi"** degani ham bo'lishi mumkin.

Eng nozik joyi — **konvert 90 000**: `test_invalid_envelope_shapes` da
`'x'*90001` **bor**, ya'ni test **qadagandek ko'rinadi**. Lekin mutatsiya yashil
chiqdi, chunki shift olib tashlanganda ham o'sha satr `json.loads` da yiqilib
**baribir** `VaultError` beradi. Ya'ni test **rad etishni** o'lchagan, **sababini**
emas — §148 va §149 naqshlarining **test tomonidagi** nusxasi.

### §150.4. Haqiqiy nuqson — **chaqiruvchining o'z xatosi kripto xatosi bo'lib xabar qilinardi**

`seal` ning `except Exception` i `_aad` ning **o'z** `VaultError` ini ham yutardi:
chaqiruvchi **o'z argumentini** noto'g'ri bergan bo'lsa ham "shifrlash
muvaffaqiyatsiz" degan xabar olardi.

| Holat | Tuzatishdan **oldin** | Tuzatishdan **keyin** |
|---|---|---|
| `seal` + kontekst 4097 bayt | `Secret encryption failed` | `Bounded encryption context required` |
| `seal` + bo'sh kontekst `{}` | `Secret encryption failed` | `Bounded encryption context required` |
| `seal` + kontekst `None` | `Secret encryption failed` | `Bounded encryption context required` |

Bu — §148 ("beshta rad etish bitta xabar") va §149 ("ikki sabab bitta xabar")
naqshining **uchinchi nusxasi**. Farqi: bu yerda sabab **chaqiruvchining o'z
argumenti**, ya'ni uni **tuzatish mumkin**, va kripto shaklidagi xabar
chaqiruvchini **noto'g'ri joydan** qidirtiradi.

**Tuzatish:** tor istisno keng istisnodan **oldin** turadi —
`except VaultError: raise`, keyin `except Exception`.

**Va `open` da bu ataylab TESKARI qoldirildi.** Sabab o'lchandi: `open` da `_aad`
**kalit id tekshiruvidan keyin** chaqiriladi. Agar kontekst xatosi alohida
nomlansa, u "kalit id noma'lum" dan **ajralib qolardi** — ya'ni hujumchi **qaysi
kalit id lar sozlanganini** bilib olardi. Bu **oracle**:

```
open(konvert, {})              -> Secret authentication failed
open(konvert, {boshqa ctx})    -> Secret authentication failed
open(konvert, juda katta ctx)  -> Secret authentication failed
open(konvert, kid='nope')      -> Secret authentication failed
```

To'rttasi ham **bir xil**. Asimmetriya **ataylab**, va endi **qadalgan**
(`test_open_does_not_reveal_key_id_membership`) — chunki aks holda keyingi
dasturchi uni "izchillik uchun" tuzatib, oracle'ni **qaytarib qo'yadi**.

### §150.5. Uch chegara **yetib borilmaydi** — o'lchandi, tuzatilmadi

1. **Konvert 90 000.** Eng katta **to'g'ri shaklli** konvert o'lchandi: maksimal
   yuk (64 000 bayt) + eng uzun kalit id (64 belgi) = **85 479 belgi**, ya'ni
   **85 479 < 90 000**. Demak bu shiftga **hech qachon** yetib borilmaydi, va
   uning rad etishi **parse xatosidan ajralmaydi** (ikkalasi ham `VaultError`).
   Uning vazifasi — `json.loads` ni katta satrdan **uzoq tutish**. Shuning uchun
   **natija emas, mexanizm** qadaldi: `json.loads` **umuman chaqirilmasligi**
   assert qilinadi. Probe bu sonni **har yurishda qayta o'lchaydi**.

2. **Kodlangan maydon 150 000.** `open` ichida konvert shifti **birinchi** uradi
   (90 000 < 150 000). `from_environment` orqali ham yetib bo'lmaydi: probe **shu
   hostning** muhit o'zgaruvchisi shiftini bisection bilan o'lchadi — **32 747
   belgi**, ya'ni **32 747 < 150 000** (Linux'da 131 072). Ya'ni bu chegara
   **chaqiruvchi tomonidan hech qayerdan yetib borilmaydi**; to'g'ridan-to'g'ri
   chaqiruv sifatida qadaldi.

3. **Ochilgan yuk 64 000.** Bunga faqat **shu modul ishlab chiqarmagan** konvert
   yetib boradi, chunki `seal` ayni shu sonni **birinchi** rad etadi. Ya'ni bu —
   "bizning chegaramizni baham ko'rmaydigan tengdosh" dan himoya. Qo'lda qurilgan
   konvert bilan qadaldi.

**Uchtasi ham tuzatilmadi** — tuzatish uchun **nuqson** kerak, bu yerda esa
**o'lchangan fakt** bor.

### §150.6. Uch chegarani **xulq-atvor umuman qaday olmaydi**

`NONCE_BYTES`, `ENVELOPE_VERSION` va `AAD_FORMAT` **ham yozuvchi, ham o'quvchi**
tomonidan o'qiladi. Shuning uchun ularni kengaytirish **ikkala tomonni birga**
suradi va round-trip **baribir o'tadi**. Probe buni o'lchadi:

```
RECORDED: a widened nonce size still round-trips
RECORDED: a widened envelope version still round-trips
RECORDED: a widened aad format still round-trips
```

Ya'ni bu uchtasi uchun **yagona pin — literal assertion**. Va bu **nazariy emas**:
ikkinchi matritsa **1/17 yashil** chiqdi va aynan `AAD_FORMAT` edi — men uni
literal tekshiruviga qo'shishni **unutilgan edim**. Ya'ni "xulq-atvor qadaydi"
degan taxmin **bir marta** allaqachon xato bo'ldi.

`AAD_FORMAT` ning tikdagi narxi **haqiqiy**: uni productionda o'zgartirish
**saqlangan har bir credentialni ochilmaydigan** qiladi, chunki AAD o'zgaradi.

### §150.7. Qaytarish matritsasi — **17/17 qizil**

Konstantalar nomlangach mutatsiya nishonlari ham **konstanta qiymati** bo'ldi.

```
key ring floor 1           RED   6.8s  FAILED (failures=1)
key ring ceiling 8         RED   6.9s  FAILED (failures=2)
key id ceiling 64          RED   7.9s  FAILED (failures=1)
key material 32 bytes      RED   2.3s  FAILED (failures=2, errors=67)
duplicate key material     RED   7.2s  FAILED (failures=1)
context ceiling 4096       RED   7.1s  FAILED (failures=1)
context must be non-empty  RED   7.1s  FAILED (failures=2)
sealed payload 64000       RED   7.0s  FAILED (failures=3)
opened payload check       RED   7.9s  FAILED (failures=1)
envelope 90000             RED   7.2s  FAILED (failures=1)
encoded field 150000       RED   7.1s  FAILED (failures=1)
envelope field set         RED   7.1s  FAILED (failures=1)
envelope version 1         RED   7.2s  FAILED (failures=1)
aad format 1               RED   8.9s  FAILED (failures=1)
nonce 12 bytes             RED   8.4s  FAILED (failures=1)
seal names caller error    RED   9.7s  FAILED (failures=1)
open stays generic         RED   8.5s  FAILED (failures=1)
restore verified: YES
```

Ikki mutatsiya **konstanta emas**, balki shu fazada tuzatilgan **ikki xulq**:
`seal` ning o'z xatosini nomlashi va `open` ning **nomlamasligi**. Qadalmasa,
ikkala tuzatish ham **jimgina qaytarilishi** mumkin edi.

### §150.8. Asbob yaxshilandi — `--check` rejimi

Naqsh xatosini **ikki marta** to'lashga to'g'ri keldi. Endi `revert_matrix.py` da
`verify()` bor va fazza skriptlari `--check` ni qabul qiladi:

```
$ python scripts/probes/audit_vault_bounds.py --check
target: api-python\platform_runtime\secret_vault.py  (6194 bytes)
  OK         key ring floor 1
  ...
17 mutations, 17 usable, 0 unusable
```

Matritsa **~2 daqiqa** oladi; naqsh tekshiruvi **millisekund**. Xato endi
kutishdan **oldin** topiladi.

### §150.9. Qo'shilgan sinov va probe

- `test_secret_vault.py`: yangi `DeclaredBoundTests` — **11 sinov**, 11 → **22**.
  Har biri chegarani **ikki tomondan** yuradi va **literalni** qadaydi.
- `scripts/probes/probe_vault_boundaries.py` — **99 xossa, 99 pass**, 9 bo'lim.
- Modul: 99 → **147 satr**; nomlangan konstanta **0 → 12**.

### §150.10. O'zim qilgan xatolar (yashirilmadi)

1. **`AAD_FORMAT` ni literal tekshiruviga qo'shmadim.** Ikkinchi matritsa
   **1/17 yashil** chiqdi va aynan shu bo'ldi. Ya'ni "qadadim" degan ishonch
   **matritsa bilan** tekshirilishi kerak — matritsa **ushlab qoldi**.
2. **Muhit o'zgaruvchisi shiftini bilmasdim.** 150 001 belgili qiymatni
   `os.environ` ga yozdim va `ValueError: the environment variable is longer than
   32767 characters` oldim. Ya'ni chegara **yetib borilmaydigan** ekanini
   **xato orqali** bildim — o'lchov kerak edi.
3. **`AESGCM.encrypt` ga `str` berdim** — `bytes` kerak. Qo'lda qurilgan konvert
   yordamchisida `.encode('utf-8')` tushib qolgan edi.
4. **Eng katta konvertni xato hisobladim** — 85 478 dedim, **85 479** chiqdi.
   Probe o'lchadi; hisobim bir belgi xato edi.

### §150.11. Saboqlar

1. **Yashil rang uch xil ma'no beradi:** qadalmagan, qo'riqchi ko'rinmas, yetib
   borilmaydigan. Uchtasini **ajratish** — ishning o'zi.
2. **Rad etish xabari ham chegara.** Repo §148 va §149 da "hamma sabab bitta
   xabar" ni **ikki marta** nuqson deb topdi. Bu yerda esa **aynan o'sha naqsh
   to'g'ri** bo'lib chiqdi — `open` uchun. Naqshni **ko'r-ko'rona** qo'llash
   xato bo'lardi; **o'lchash** kerak.
3. **Asimmetriya niyat bo'lsa, u ham qadalishi kerak.** Aks holda keyingi
   dasturchi uni "izchillik" deb tuzatadi va **oracle'ni qaytaradi**.
4. **Bir son ikki tomonda o'qilsa, xulq-atvor uni qaday olmaydi.** `NONCE_BYTES`,
   `ENVELOPE_VERSION`, `AAD_FORMAT` — yagona pin **literal**.
5. **Test "rad etildi" ni o'lchab, "nima uchun" ni o'lchamasa, chegara qadalmagan
   bo'lib qoladi.** `'x'*90001` aynan shu holat edi.
6. **Naqsh xatosini ikki marta to'lash — asbobni tuzatish signali.** `--check`
   shundan tug'ildi.

### §150.12. Yakuniy baseline — o'lchandi

```
Ran 2814 tests in 576.803s

FAILED (failures=1, errors=11, skipped=1)
```

Imzo (`failures=1, errors=11, skipped=1`) **o'zgarmadi**. Devor vaqti yukka
bog'liq: §149 da 270.9 s edi, bu yurishda 576.8 s — test soni va imzo bir xil.

| Ko'rsatkich | Qiymat |
|---|---|
| Testlar | **2803 → 2814** = **+11** (`DeclaredBoundTests`), boshqa o'zgarish yo'q |
| Imzo | `failures=1, errors=11, skipped=1` — **o'zgarmadi** |
| `secret_vault.py` | 99 → **147 satr**; nomlangan konstanta **0 → 12** |
| Matritsa | 13 → **17 mutatsiya**, **17/17 qizil**, 0 yashil, 0 o'lchanmagan |
| Probe | **99 xossa, 99 pass** |

---

## §151. Fazza qirq oltinchi — tashqi provayder transporti va **shakli hech narsa bilan qadalmagan client id**

To'rt modul birgalikda o'lchandi, chunki ular bir xil savolga javob beradi: *provayder
javobiga qanchalik ishonish mumkin?* — `google_oauth.py`, `model_transport.py`,
`model_response.py`, `mcp.py`.

### §151.1. Chegaralar, va ularni kim tekshirardi

O'n to'qqizta chegara: javob hajmi, so'rov tanasi, credential uzunligi, xato tanasi,
client id, redirect URI, subject, transport muddati, konfiguratsiya sxemasi versiyasi,
lokal port, JSON chuqurligi, JSON tugunlari, majburiy tanlovlar, protokol versiyalari.

Ularning **hammasi inline literal** edi. Ya'ni: `1_000_000`, `64000`, `16000`, `25`.

### §151.2. Haqiqiy nuqson — **client id shakli o'chirilishi mumkin edi**

Matritsa `client id suffix` mutatsiyasini **YASHIL** qaytardi. Ya'ni
`[A-Za-z0-9._-]+\.apps\.googleusercontent\.com` qoidasini **butunlay o'chirish**
mumkin edi va to'plam jim qolardi.

Sabab: mavjud chegara testi faqat **to'g'ri shakldagi** id bilan oziqlangan edi — u
**uzunlikni** qadaydi, **shaklni** o'lchamagan. Bu §II fazasining (`truncated`)
takrorlanishi: chegara bor, uni o'lchaydigan test yo'q.

Tuzatish: `test_client_id_must_be_a_google_client_id`. Qayta yurish → **RED
(failures=5)**.

### §151.3. Qaytarish matritsasi — **23/23 qizil, 1 o'lchangan**

24 mutatsiyadan 23 tasi qizil. Bittasi — `MAX_PORT = 65535` — **ataylab
tuzatilmadi**: `urlsplit` 65 535 dan katta portni **taqqoslashdan oldin** rad etadi,
ya'ni konstanta kengaytirilsa ham natija o'zgarmaydi. Bu **o'lchov muvaffaqiyatsizligi**,
kod nuqsoni emas — va shunday yozib qo'yildi.

---

## §152. Fazza qirq yettinchi — planner, speech, baza o'qish, CRM reconcile

### §152.1. Nima uchun bu to'rtlik

Inventar to'rttasini ham "na probe, na faza" deb belgilagan edi. Har biri bir raqam
biror narsani hal qiladigan joy: model qancha kontekst ko'radi, qancha audio
yuboriladi, qancha qator qaytadi, aniq moslik qoidasidan oldin qancha nomzod olinadi.

### §152.2. Ikki chegara **boshqa shaklda** qadaldi — va shakl topilma

- `DEFAULT_PORT` faqat konfiguratsiyada port **yo'qligi** sifatida yetib boriladi,
  shuning uchun mutatsiya **rad etish** bilan emas, **qiymat** tekshiruvi bilan
  ushlanadi. U qo'shnilaridan **ataylab** boshqacha o'qiladi.
- `MIN_SPEED` — modul chaqiruvchiga ishonishni to'xtatgan chegara. Qo'shni `bool`
  rad etilishi **alohida** qadalgan, chunki Python'da `True` — bu `int`, va oddiy
  son oralig'i tekshiruvidan **o'tib ketardi**. Modul bu nuqsonni allaqachon
  chetlab o'tgan edi; endi qadaydi.

### §152.3. Natija — **28/28 qizil, 0 yashil**

Bu fazada **bitta ham** chegara yashil chiqmadi. Sabab asbobda emas, modullarda:
har bir raqam allaqachon biror narsani rad etardi, yetishmayotgani — buni **aytgan
test** edi.

### §152.4. Asbob o'zgardi — **imzo asosidagi baseline**

Bu fazada nazorat yurishi **YASHIL bo'lmadi**: naqshga `test_foundation_v02.test_mount_scope`
kirdi, u esa POSIX mount semantikasi talab qiladi va Windows'da hech qachon yashil
bo'lmaydi. Ya'ni "nazorat yashil bo'lishi shart" qoidasi **hech qachon** o'lchay
olmasdi.

`revert_matrix.py` ga `signature()` va `AUTO_BASELINE` qo'shildi: nazorat **yashil**
bo'lishi emas, **yozib olingan imzoni takrorlashi** shart. Har bir mutatsiya esa
o'sha imzoni **o'zgartirishi** shart — bu yashillikdan **qattiqroq**, chunki
oldindan mavjud xatoni o'chirib tashlagan mutatsiya ham ushlanadi.

---

## §153. Fazza qirq sakkizinchi — boshqaruv tekisligi va **10× kengaytirilgan avtonomiya shifti**

### §153.1. Nega aynan bu fayl

`app/platform_api.py` — har bir tenant yozuvi o'tadigan yagona eshik: **717 satr,
88 `Field(...)`**. Inventar uni "eng katta audit qilinmagan modul" deb belgilagan
edi, va sabab bir qatorda ko'rinadi:

```
grep -l 'le=10' runtime_tests/*.py        →  0 fayl
grep -l 'max_length=20' runtime_tests/*.py →  0 fayl
```

Ya'ni **hech bir test umuman raqam aytmasdi**. Savol shu sababdan boshqacha
qo'yildi: *avtomatlashtirish mijozga qanchalik qattiq tegishi mumkinligi chegarasini
bitta ham test qizarmasdan kengaytirish mumkinmi?* — **ha**.

### §153.2. Nima qilindi

**48 shakl**, **81 chaqiruv joyi** nomlangan konstantaga aylantirildi. Konstantalar
uch bo'limga ajratildi: **shift** (matn, vektor, pul, qadam, vaqt), **avtonomiya
shiftlari** (qayta aloqa, eskalatsiya, brifing), **standart qiymatlar**.

Invariant **mutlaq** qilindi: modulda bitta ham `Field(...)` ichida raqamli literal
qolmadi, va buni `NoInlineBoundTests` tekshiradi. Busiz yuqoridagi ikki qatlam
"vakillik" bo'lib qolardi — **to'liqlik** emas.

### §153.3. Strukturaviy test **uchta haqiqiy xatoni** ushlab qoldi

Umumiy naqsh almashtirishlari bir xil *qiymatga* ega, lekin **boshqa ma'noli**
maydonlarni birlashtirib qo'ygan edi. "E'lon qilingan-u ishlatilmagan konstanta"
testi ularni topdi:

| Konstanta | Nima bo'lgan edi |
|---|---|
| `MAX_DISPLAY_NAME_CHARS` | `display_name` `MAX_KEY_CHARS` ga tushgan |
| `MAX_EXTERNAL_ID_CHARS` | `external_ref`, `external_id` `MAX_KEY_CHARS` / `MAX_MODEL_CHARS` ga tushgan |
| `MAX_QUERY_CHARS` | bilim `query` si `MAX_EVIDENCE_CHARS` ga tushgan |

Qiymat bir xil (500, 256) bo'lgani uchun **hech bir xulq o'zgarmagan** — lekin
kelajakda bittasi o'zgarsa, ikkinchisi ham **jimgina** o'zgarardi.

### §153.4. Blast radius **o'lchandi, taxmin qilinmadi**

Uchta eng xavfli chegara yigirma daqiqalik naqsh bilan mutatsiya qilindi:

| Mutatsiya | `test_reengagement` + `test_escalation` + `test_briefing` |
|---|---|
| `MAX_REENGAGEMENT_PER_CYCLE` 20 → **200** | **YASHIL** |
| `MAX_ESCALATION_PER_CYCLE` 50 → **500** | **YASHIL** |
| `MAX_COOLDOWN_SECONDS` 2 592 000 → **25 920 000** | **YASHIL** |

Bir siklda **10 barobar ko'proq** mijozga tegish va anti-spam kutish muddatini
**10 barobar** qisqartirish — bu uch modul uchun **butunlay ko'rinmas**. Ular
endpoint'lar **orqasidagi** siklni o'lchaydi, **oldidagi** shartnomani emas. Shu
sabab matritsa naqshi ataylab **bitta fayl**: to'rt modul 2 daqiqalik matritsani
70 daqiqaga aylantirardi va **hech narsani isbotlamasdi**.

### §153.5. Qaytarish matritsasi — **61/61 qizil**

**0 yashil, 0 o'lchanmagan.** Birinchi marta **barcha standart qiymatlar ham**
qadalgan (16 mutatsiya). `DEFAULT_*` — bu chegara emas, **siyosat**: operator
tanlamaganda tizim **o'zi** nima qiladi.

### §153.6. Yakuniy o'lchov

| Ko'rsatkich | Qiymat |
|---|---|
| Mutatsiya | **61**, **61/61 qizil**, 0 yashil, 0 o'lchanmagan |
| Yangi sinov | `test_control_plane_bounds.py` — **50 sinov**, uch qatlam |
| `platform_api.py` | 717 → **913 satr**; nomlangan konstanta **0 → 90** |
| Imzo | `failures=1, errors=11, skipped=1` — **o'zgarmadi** |

### §153.7. O'zim qilgan xatolar

**`min_length=1` ni tashlab ketdim** — keyin nomladim (`MIN_NON_EMPTY`), chunki u
ham chegara ("bo'sh bo'lmasin"), va nomlanmasa struktura invarianti **mutlaq**
bo'lmasdi, ya'ni bitta istisno qolardi. **`MAX_EXTERNAL_ID_CHARS` va
`MAX_QUERY_CHARS` ni e'lon qildim, lekin ishlatmadim** — strukturaviy test ushladi.
**Umumiy naqsh almashtirishga haddan tashqari ishondim** — uchtasi birlashib ketdi.

---

## §154. Fazza qirq to'qqizinchi — Windows uchun bloklangan yuza: **11 emas, 12**

### §154.1. Yozuvning o'zi xato edi

| | |
|---|---|
| Inventar da'vosi | `failures=0, errors=11, skipped=2` |
| **O'lchangan** | `failures=1, errors=11, skipped=1` |

Uch maydondan **bittasi** to'g'ri. Sabab mazmunli:
`test_macos_bundle.test_all_files_private` — **failure**, **error** emas: u
yetishmayotgan primitivni chaqirmaydi, **ruxsat bitlarini** tekshiradi
(`st_mode & 0o077 == 0`), shuning uchun boshqacha yiqiladi va "error" sanovidan
tushib qolgan. `skipped=2` esa umuman yo'q: to'plamdagi **yagona** `skipTest` —
`test_whatsapp_inbound` da, va u platforma uchun emas, **runtime** uchun.

Ya'ni **o'zini yozuv deb e'lon qilgan hujjat ichida** raqam surilib ketgan.

### §154.2. Bitta yetishmayotgan primitiv yettitasini bloklaydi

| Sabab | Sinovlar |
|---|---|
| `os.O_NOFOLLOW` yo'q | **7** |
| `os.mkfifo` yo'q | 2 |
| `fcntl` moduli yo'q | 1 |
| POSIX mount semantikasi (`test_foundation_v02`) | 1 |
| POSIX ruxsat bitlari (`test_macos_bundle`) | 1 |

`O_NOFOLLOW` — to'plamning **yarmidan ko'pini** bloklayotgan yagona sabab, chunki u
`open()` ning simlinkni kuzatmasligini ta'minlaydi va `portable_fs` butun
descriptor-identifikatsiya tekshiruvini shunga qaraydi.

### §154.3. Yechim — **sabab bo'yicha** qadash, sinovlarni qayta yurgizmasdan

`runtime_tests/test_platform_baseline.py` (**7 sinov**) ro'yxatni shunday qadaydi:
Windows'da har bir sabab **hamon** amal qilishi shart, POSIX'da esa **hech biri**
amal qilmasligi shart. Ya'ni kim `os.O_NOFOLLOW` ni shim qilsa yoki loyiha Linux
runner'ga o'tsa, modul **qizil** bo'ladi va "yozuv eskirgan" deydi.

Ro'yxatdagi har bir ID **import qilinib tekshiriladi**, ya'ni xato yozilgan ID
12 gacha sanab, hech narsani hujjatlashtirmasligi mumkin emas.

**O'tkazib yuborish — bu o'tish emas.** O'n ikkisi Windows'da **tekshirilmagan**,
yashil emas.

### §154.4. Yo'l-yo'lakay topilgan nuqson — `integration_tests` **yig'ilmasdi**

```
pytest integration_tests --collect-only
→ 86 tests collected, 1 error
→ ERROR integration_tests/test_connector_authority_http.py - app.config.ConfigError
```

Sakkiz moduldan **yettitasi** `ENV` ni o'zi o'rnatadi, bittasi yo'q.
`test_connector_authority_http.py` ning 4-qatori `app.platform_api` ni import
qiladi, `ENV` ni o'rnatadigan modulga esa 6-qatorda yetadi — pytest esa uni alifbo
bo'yicha **birinchi** yig'adi. `app.platform_api` → `app.auth` import vaqtida
konfiguratsiyani tekshiradi va **fail-closed** yopiladi.

Tuzatish: `integration_tests/conftest.py` — pytest har qanday test modulidan
**oldin** `conftest` ni import qiladi. Natija: **95 sinov, 95 pass, 50 s.**

### §154.5. Yakuniy baseline

```
Ran 2949 tests in 425.883s

FAILED (failures=1, errors=11, skipped=1)
```

| Ko'rsatkich | Qiymat |
|---|---|
| Testlar | 2814 → **2949** = **+135** |
| Imzo | `failures=1, errors=11, skipped=1` — **o'zgarmadi** |
| Bloklangan yuza | **12**, endi `test_platform_baseline.py` qadaydi |
| `integration_tests` | **95 pass** (ilgari yig'ilmasdi) |
| Devor vaqti | 576.8 s → **425.9 s** — test **ko'paydi**, vaqt **qisqardi** |
## §155. Fazza ellikinchi — `app/` qatlami: **o'qilmaydigan to'plamdagi qadam — qadam emas**

### §155.1. "Qadalgan" ko'ringan ikki chegara

`MAX_PERSONA_CHARS` (`packs.py`) va `MAX_QUEUE` (`runner_ws.py`) — ikkisi ham
`tests/` da qadalgan edi. Muammo qadamda emas, **to'plamda**:

| `.github/workflows/verify.yml` | Buyruq | Gate? |
|---|---|---|
| 15-qator | `unittest discover -s runtime_tests` | **ha** |
| 33-qator | `pytest integration_tests -q` | **ha** |
| — | `api-python/tests/` | **hech qayerda yo'q** |

`tests/` esa **qizil**, chunki u **ataylab bekor qilingan** API ni sinaydi va
marshrutlar `410 Gone` qaytaradi:

```
Legacy runner disabled; use /platform/runner/ws and platform tasks
Legacy mutation retired; use platform API
```

Ya'ni qadam `tests/` da yashaydi-yu, `tests/` ni hech bir gate yurgizmaydi.
**Bu qadam emas — da'vo.** `MAX_PERSONA_CHARS` ni `8000` → `80000` qilish hech
qanday signal bermasdi; `MAX_QUEUE` ni `100` → `1000` qilish ham.

### §155.2. Sanab chiqilgan chegaralar

| Modul | Chegara | Qiymat | Oldin |
|---|---|---|---|
| `auth.py` | token shifti | 86 400 s | qadalmagan |
| `auth.py` | token poli | 1 s | **nomsiz** |
| `auth.py` | sessiya tokeni umri | 900 s | **nomsiz** (ternary ichida) |
| `identity_store.py` | sessiya shifti / poli | 2 592 000 / 60 s | qadalmagan / **nomsiz** |
| `identity_store.py` | throttle limit / oyna | 20 / 900 s | **nomsiz** |
| `identity_store.py` | taklifnoma default / pol / shift | 86 400 / 300 / 604 800 s | **nomsiz** |
| `identity_store.py` | e-mail shifti | 320 belgi | **nomsiz** |
| `identity_store.py` | parol poli / shifti | 12 / 256 | **nomsiz** |
| `identity_store.py` | token poli / shifti | 20 / 256 | **nomsiz** |
| `identity_store.py` | scrypt xarajat / blok / parallel | 16 384 / 8 / 1 | **nomsiz** |
| `identity_store.py` | kalit uzunligi / tuz | 32 / 16 bayt | **nomsiz** |
| `identity_store.py` | oxirgi owner qo'riqchisi | 1 | **nomsiz** |
| `limits.py` | kunlik shift / pol | 1000 / 1 | **nomsiz** |
| `limits.py` | hisoblagich TTL | 86 400 s | **nomsiz** |
| `limits.py` | ogohlantirish ulushi | 0.8 | **nomsiz** (ikki joyda yozilgan) |
| `packs.py` | persona shifti | 8 000 belgi | faqat `tests/` da |
| `telegram.py` | webhook tanasi shifti | 1 000 000 B | qadalmagan |
| `trace.py` | rotatsiya ostonasi | 5 MiB | qadalmagan |
| `runner_ws.py` | navbat / natija shifti | 100 / 1000 | faqat `tests/` da / qadalmagan |

### §155.3. Beshta son — bu sozlama emas, **xarajat**

`SCRYPT_N`, `SCRYPT_R`, `SCRYPT_P`, `SCRYPT_DKLEN`, `SALT_BYTES`. Bu yerda
`0.8` ni `0.08` qilish — siyosat; `SCRYPT_N` ni `16384` → `1024` qilish esa
**har bir saqlangan parolni buzish narxini 16 barobar arzonlashtirish**.

To'plamda bu beshta sonni **hech narsa o'qimasdi**, ya'ni xarajat faktorini
tushirishning yagona signali — loginlarning tezlashishi bo'lardi. Endi
`test_app_layer_bounds` ularni so'zma-so'z qadaydi.

### §155.4. Instrument o'z sidecar'ini o'chira olmagani uchun o'lchov o'ldi

Bu fazaning eng qimmatli topilmasi chegara emas, **asbob** haqida.

Matritsa birinchi moduldan keyin to'xtadi — 31 mutatsiyadan **3 tasi**
o'lchandi:

```
target      : api-python\app\auth.py
CONTROL                GREEN           OK
token ceiling 86400    RED               5.9s
token floor 1          RED               6.3s
session token lifetime 900 RED            5.9s
restore verified: YES
```

Qolgan olti modul haqida **birorta satr yo'q**, va chiqish kodi `0`. Sabab:
`revert_matrix.main` oxirida `os.remove(sidecar)` chaqiriladi, host siyosati
esa ommaviy o'chirishni to'sadi:

```
[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]
  {"count":220,"threshold":50,"scope":"turn","targets":["...auth.py.matrix-baseline"]}
```

Istisno `main` dan yuqoriga chiqib, faza skriptining `for` sikli uziladi.
Ya'ni **asbob o'zini tozalay olmagani uchun o'ldi va bu haqda hech narsa
demadi** — bu §148 dagi "ikki o'qilmagan rejim yashil deb o'qilgan" nuqsonining
boshqa ko'rinishi.

Tuzatish — `revert_matrix.drop_sidecar()`: `OSError` yutiladi, xabar
bosiladi, o'lchov davom etadi. Sidecar eskirib qolishi xavfsiz: keyingi yurish
uni jonli fayl bilan solishtiradi va yo tiklaydi, yo ustidan yozadi.

### §155.5. Natija — 31/31 qizil

| Modul | Mutatsiya | Natija |
|---|---|---|
| `app/auth.py` | 3 | 3 RED |
| `app/identity_store.py` | 19 | 19 RED |
| `app/limits.py` | 4 | 4 RED |
| `app/packs.py` | 1 | 1 RED |
| `app/telegram.py` | 1 | 1 RED |
| `app/trace.py` | 1 | 1 RED |
| `app/runner_ws.py` | 2 | 2 RED |
| **Jami** | **31** | **31 RED, 0 GREEN, 0 o'lchanmagan** |

Har modulda `CONTROL GREEN` va `restore verified: YES`. Sidecar qolmadi,
mutatsiya qoldig'i yo'q (`grep` bilan tekshirildi).

O'lchov to'plami ikki fayl: `test_app_layer_bounds` (48 sinov — qadamlar) va
`test_identity_store` (6 sinov — **iste'molchi**: u haqiqiy sessiya, taklifnoma
va parol yaratadi, shuning uchun kengaytirilgan TTL yoki bo'shatilgan parol
poli uni **chaqiruvchida** sezadi, faqat uni nomlagan assertion'da emas).
`test_identity_hardening` uchinchi bo'lardi va **o'lchov bilan** chiqarildi: u
har yurishda 12 s yeydi, ya'ni 3 daqiqalik matritsani 10 daqiqalikka aylantiradi
va qo'shimcha qamrov bermaydi.

### §155.6. Struktura sinovi beshta chegarani topdi — o'zim qoldirganlarini

`test_the_promoted_modules_carry_no_stray_literal_in_a_guard` qo'shilgandan
keyin **beshta** haqiqiy nomsiz chegara chiqdi, hammasi `identity_store.py` da:

```
len(value) > 320                      -> MAX_EMAIL_CHARS
12 <= len(password) <= 256            -> MIN_PASSWORD_CHARS / MAX_PASSWORD_CHARS
1  <= len(password) <= 256            -> MIN_CANDIDATE_PASSWORD_CHARS
20 <= len(value) <= 256               -> MIN_TOKEN_CHARS / MAX_TOKEN_CHARS
len(owners) <= 1                      -> LAST_OWNER_GUARD
```

Ya'ni "nomlangan" deb e'lon qilingan modulda ham sonlar qolgan edi. `limits.py`
da ham `v > 0` → `v >= MIN_DAILY_LIMIT` tuzatildi.

### §155.7. Yo'l-yo'lakay: `tests/` — 33 qizil, uch sabab

`tests/` ni o'lchash paytida ma'lum bo'ldi: **35 emas, 33 qizil**
(`33 failed, 172 passed, 4 skipped, 9.6 s`) — ikki sinov `tzdata` bilan
yashilga o'tdi.

| Sabab | Sinovlar |
|---|---|
| Bekor qilingan marshrut → `410 Gone` | **10** |
| Eskirgan javob shakli (`KeyError`: `access_token` 8, `task_id` 3, `reply` 2, `approval_id` 1, `stopped` 1) | **15** |
| Legacy runner WebSocket (`WebSocketDisconnect`, yopilish kodi `4401`) | **4** |
| Kontrakt/mazmun surilishi (`AssertionError`) | **3** |
| Auth statusi (`assert 401 == 403`) | **1** |
| **Jami** | **33** |

`tzdata` — **o'lchov sodiqligi** nuqsoni edi, kod nuqsoni emas:
`requirements.txt:9` da `tzdata>=2024.1` **e'lon qilingan**, lekin lokal
venv'da o'rnatilmagan edi. CI (Linux) uni o'rnatadi, Windows esa yo'q. Ya'ni
mening baseline'im CI muhitiga mos kelmasdi. O'rnatildi: `35 → 33 qizil`,
`170 → 172 yashil`.
### §155.8. Eng qimmatli topilma: §154 ning **o'z yozuvi** ikki marta xato edi

§154 `test_platform_baseline.py` ni **aynan shu maqsadda** yozgan edi: bloklangan
yuzaning soni surilib ketmasligi uchun. U **o'zi surilib ketdi**.

| | §154 yozuvi | §155 o'lchovi |
|---|---|---|
| Bloklangan sinovlar | 12 | **13** |
| `O_NOFOLLOW` | 7 | **6** |
| `mkfifo` | 2 | 2 |
| `fcntl` | 1 | 1 |
| `posix` | 2 | 2 |
| **simlink imtiyozi** | — | **2** |
| To'plam imzosi | `errors=11` | `errors=12` |

Ikki xato, va ikkisi ham boshqa turdagi:

1. `test_root_symlink_replacement_denied` ro'yxatda **umuman yo'q edi** — ya'ni
   son birga kam edi va `test_the_blocked_surface_is_twelve_tests` buni
   "to'g'ri" deb tasdiqlab turgan edi.
2. `test_escape_symlink_denied` `O_NOFOLLOW` deb yozilgan, lekin u o'sha kodga
   **yetib ham bormaydi**: traceback uning o'z `setUp` ida, `os.symlink` da
   o'lganini ko'rsatadi.

Ikkisi ham **boshqa** platforma faktidan o'ladi va yozuvda uning **nomi yo'q
edi**: Windows simlink imtiyozi — `OSError` `WinError 1314` bilan (Developer Mode
yoki ko'tarilgan token talab qiladi). Endi u `symlink_privilege` nomi bilan
turadi.

**Nega birinchi versiya buni ko'ra olmadi** — bu fazaning asosiy saboqi va u
umumiy:

    U shartning ROST bo'lishini tekshirdi. Shartning AYNAN SABAB
    ekanini hech qachon tekshirmadi.

`not hasattr(os, 'O_NOFOLLOW')` bu hostda **rost** — o'sha sinovni o'ldirgan
narsa u bo'lsa ham, bo'lmasa ham. Ya'ni noto'g'ri yozilgan qator predikatni
qanoatlantiradi va **o'tib ketadi**. Bu §155.1 dagi "o'qilmaydigan to'plamdagi
qadam" ning bir oiladagi qarindoshi: **tekshirilgan ko'ringan narsa
tekshirilmagan bo'lishi mumkin.**

Yechim — `test_each_recorded_reason_is_the_actual_cause`: u har bir bloklangan
sinovni **yurgizadi** va ko'tarilgan istisnoni o'sha qatordagi sababga
solishtiradi (`O_NOFOLLOW` qatori `O_NOFOLLOW` `AttributeError` bilan o'lishi
shart; `symlink_privilege` qatori `WinError 1314` bilan). Endi "ishonchli
ko'ringan" sabab **yiqiladi**.

**Qo'riqchi tekshirildi:** `test_escape_symlink_denied` ni `O_NOFOLLOW` ga
qaytarib mutatsiya qilinsa — **3 sinov qizil**. Ya'ni bu qo'riqchi haqiqatan
ushlaydi, shunchaki yashil turmaydi.

Yo'l-yo'lakay **o'sha tuzoqning ikkinchi nusxasi**: `TestResult.errors` istisno
obyektini emas, **formatlangan traceback satrini** saqlaydi, ya'ni
`errors[0][1][1]` — bu **satrning ikkinchi belgisi**. Tekshiruvchi sinovning
o'zi shu xatoni qildi va 11 qizil berdi. `_Capture` sinfi endi `addError` dan
obyektni oladi.

Natijada `RECORDED_SIGNATURE` ham tuzatildi:
`{'tests': 3000, 'failures': 1, 'errors': 12, 'skipped': 1}` — va §154 ning
birinchi yozuvi `FIRST_RECORD` bo'lib modulda **saqlanib qoldi**, shunda ikkinchi
tuzatish ham yashirin tahrir emas, auditlanadigan dalil bo'ladi.
### §155.9. `verify_offline.py` — tugata olmaydigan gate, va **socket'siz to'plamga solgan socketim**

Ikki nuqson: biri asbobda, biri **mening testlarimda**. Ikkisi ham bir xil
saboqqa chiqadi.

**Asbobda.** Gate `UnicodeDecodeError` bilan yiqildi, oxirida esa
`TypeError: data must be str, not NoneType`:

```
UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97 in position 26494
  File "scripts/verify_offline.py", line 141, in run
    (output / (name + '.log')).write_text(text, encoding='utf-8')
TypeError: data must be str, not NoneType
```

`0x97` — bu `[WinError 1314] Клиент не обладает требуемыми правами` matnidagi
kirill bayti. O'quvchi thread `UnicodeDecodeError` ko'taradi, `result.stdout`
`None` bo'lib qoladi, `write_text(None)` esa `TypeError` beradi. Ya'ni **gate
o'zi tekshirayotgan platformada tugata olmaydi**, va sabab uning o'zida emas,
kodlashda.

Sabab: `child_env` `LANG=C.UTF-8` o'rnatadi, lekin `PYTHONIOENCODING` yoki
`PYTHONUTF8` ni emas. Bola o'z lokali bilan yozadi, ota-ona o'z afzal ko'rgani
bilan o'qiydi — va **ikkisi kodlash haqida kelishmagan**. Tuzatish:
`errors='replace'` va `result.stdout or ''`. Buzilgan bir qator log —
tugata olmaydigan gate'dan yaxshi.

**Mening testlarimda.** Tuzatishdan keyin gate **yurgizildi** va uchta yangi
xato bilan qizil bo'ldi:

```
RuntimeError: Offline verification: network disabled
```

Uchtasi ham mening `TelegramBodyCeilingTests` im. Ular
`starlette.testclient.TestClient` orqali yozilgan edi, va `runtime_tests` da
`TestClient` **hech qayerda ishlatilmagan** — uni faqat `integration_tests`
ishlatadi, va u aynan shu sabab bilan offline gate'dan **chiqarilgan**
(`verify_offline.py` `socket.connect`, `socket.getaddrinfo` va `socket.sendto`
ni taqiqlovchi audit hook o'rnatadi).

Ya'ni to'plam **socket'siz bo'lgani uchun** socket'siz emas edi — **hech kimga
socket kerak bo'lmagani uchun** socket'siz edi. Men socket kerak qildim, va gate
buni darhol aytdi.

Tuzatish **ikki qadam** bo'ldi, chunki birinchisi yetmadi:

1. `TestClient` olib tashlandi — korutina to'g'ridan-to'g'ri chaqiriladi.
2. Lekin `asyncio.run` ham ishlamadi: **Windows'da event loop'ning o'zi socket**.
   `ProactorEventLoop` ham, `SelectorEventLoop` ham o'z self-pipe'ini
   `socket.socketpair()` dan quradi, audit hook esa uni `socket.connect` deb
   ko'radi. Shuning uchun korutina **qo'lda**, `send(None)` bilan yuritiladi.

Qo'lda yuritish xavfsiz, chunki handler **hech qachon to'xtamaydi**: uning
yagona `await` i — stub `request.body()`, u esa hech narsani kutmasdan qaytadi.
Agar kelajakda route haqiqiy narsani kuta boshlasa, `send` `StopIteration`
o'rniga qiymat qaytaradi va test **baland ovozda yiqiladi**, osilib qolmaydi.

Tekshirildi: modul **48 sinov** — audit hook **bilan ham**, usiz ham **OK**.

**Yangi qo'riqchi:** `OfflineSuiteTests` `runtime_tests` da `TestClient` yoki
`httpx` importini taqiqlaydi. `urllib.request` **ataylab** ro'yxatda emas:
`test_custom_http_adapter` va `test_onec_adapter` uni faqat
`OpenerDirector.open` ni soxta `HTTPError` bilan almashtirib, xabar tozalanganini
tekshirish uchun import qiladi — ulanish yo'q, va taqiq **haqiqiy testni yolg'on
qizil** qilardi. Qo'riqchi statik, ya'ni import qatorida yiqiladi, birinchi tarmoq
chaqiruvida emas.

**Qo'riqchi tekshirildi:** `test_retry.py` ga `TestClient` importi qo'shilsa,
test **fayl nomini aytib** yiqiladi (`['test_retry.py: TestClient']`).

### §155.10. Node runner ham **xuddi shu sinfda** bloklangan

`node --test apps/runner/test.js` — CI ning bir qismi — Windows'da **24 dan 11
tasi** yiqiladi:

```
Error: Private single-owner file required
  apps/runner/runner.js:92  privateFile()
# tests 24   # pass 13   # fail 11
```

Sabab Python tomonidagi bilan **bir xil**, faqat boshqa tilda: `privateFile`
`(meta.mode & 0o077) === 0` ni talab qiladi, Windows esa POSIX ruxsat bitlarini
**modellashtirmaydi** — `fs.chmodSync(f, 0o600)` NTFS'da ACL bilan ishlaydi va
`mode` deyarli hech narsa qilmaydi.

Ya'ni bloklangan yuza **ikki tilda** mavjud:

| Til | Bloklangan | Qadalganmi |
|---|---|---|
| Python (`runtime_tests`) | **13 / 3000** | ha — `test_platform_baseline.py` |
| Node (`apps/runner`) | **11 / 24** | **yo'q** — hech qayerda yozilmagan |

Python tomonidagisi qadalgan, Node tomonidagisi esa **faqat shu fazada
o'lchandi**. Uni qadash §156 uchun ish: `node --test` CI'da `ubuntu-latest` da
yuguradi va u yerda yashil, Windows'da esa 11 tasi **tekshirilmagan**.

### §155.11. Yakuniy o'lchov

```
Ran 3000 tests in 172.327s

FAILED (failures=1, errors=12, skipped=1)
```

13 yomon sinov — **hammasi** ma'lum bloklangan yuza, **bittasi ham** shu fazada
yozilgan moduldan emas (`grep -c "app_layer_bounds"` → `0`).

| Ko'rsatkich | Qiymat |
|---|---|
| Testlar | 2949 → **3000** = **+51** |
| Imzo | `errors=11` → **`errors=12`** — chunki 13-sinov **haqiqatan** o'lchanmagan edi |
| Bloklangan yuza | 12 → **13**, sabab bo'yicha qadalgan |
| `verify_offline.py` | **tugata olmaydi → tugatadi**; `python_runtime` va `node_runner` Windows'da FAIL (platforma, kod emas) |
| Devor vaqti | 425.9 s → **172.3 s** — sabab o'lchanmagan, shuning uchun da'vo qilinmaydi |
### §155.12. `MANIFEST.sha256` — gate **o'zi aytgan joyda** qizil edi

`verify_manifest.py` ning birinchi qatori: *"Run on a freshly extracted release
ZIP."* Toza `git archive HEAD` eksportida o'lchandi:

```
status: FAIL
checked: 498
hash mismatches: 403
```

498 dan **403 tasi** mos kelmadi, va **hammasi matn fayllari** edi. Sabab
`core.autocrlf`: manifest CRLF tutgan Windows ishchi daraxtida yasalgan, bloblar
esa — va shuning uchun har qanday Linux checkout yoki ZIP eksport — **LF**
tutadi.

Ya'ni gate **o'zining hujjatlashtirilgan kiritmasida** qizil edi. Bu loyihaning
o'z shikoyati bilan aynan bir xil holat, `generate_manifest.py` hujjatida
yozilganidek: *"the gate was red for so long that red stopped carrying
information"*. Va u **birinchi** qadamda yurgiziladi — `.github/workflows/verify.yml:14`,
`ubuntu-latest` da — ya'ni CI ning birinchi tekshiruvi ham qizil bo'lardi.

Tuzatish: `digest()` endi **mazmunni** xeshlaydi, uni yozgan platformaning qator
oxirini emas — CRLF → LF normallashtiriladi. `generate_manifest.py` shu
funksiyani **import qiladi**, ya'ni generator bilan tekshiruvchi hech qachon
kelishib qololmaydi.

Oqim bilan o'qiladi, **bir baytli `carry`** bilan: bir chunk `\r` bilan tugab,
keyingisi `\n` bilan boshlansa, oddiy `replace` ularni buklay olmaydi va orada
begona CR qolib ketardi.

**Tekshirildi:**

| Yer | Natija |
|---|---|
| Ishchi daraxt (aralash CRLF/LF) | **PASS** — 502 fayl |
| Toza LF eksport (`git archive` → `tar -x`) | **PASS** — 502 fayl |
| Eski xulq (mutatsiya: `digest()` raw'ga qaytarildi) | **403 mismatch** — o'sha raqam |
| 4 yangi sinov, o'sha mutatsiya bilan | **3 qizil** |

Yangi sinovlar `scripts/test_manifest.py` da: CRLF va LF **bir xil** xesh beradi;
**yakka `\r` hamon mazmun** (faqat CRLF buklanadi, chunki u platforma artefakti,
yakka CR esa muallif tanlagan bayt); normalizatsiya **chunk chegarasidan** o'tadi;
va CRLF'da yozilgan manifest LF'da **PASS** beradi — ya'ni "bu yerda yoz, u yerda
tekshir" xossasi qadalgan.
## §156. Fazza ellik birinchi — tasdiq navbati va avtonomiya zinapoyasi: **gatesiz to'plam yagona qoplama edi**

### §156.1. §155 topilmasi qayerga tegishli ekani o'lchandi

§155 shunday dedi: `tests/` da qadalgan chegara — qadalgan emas, chunki uni **hech
bir gate yurgizmaydi**. Bu fazada o'sha gapning **manzili** aniqlandi:

| Modul | Gate ichida kim sinaydi | Umuman kim sinaydi |
|---|---|---|
| `app/ladder.py` → `LadderStore` | **hech kim** | `tests/conftest.py`, `tests/test_stage2.py` |
| `app/approvals.py` → `FileApprovalStore` | **hech kim** | `tests/conftest.py`, `tests/test_stage1.py`, `tests/test_stage2.py`, `tests/test_gaps.py`, `tests/test_audit_fixes.py` |

`runtime_tests` va `integration_tests` bo'ylab qidiruv bu ikki store'ning boshqa
iste'molchisini topmaydi. Ularni import qiladigan `app/` modullari — `main.py`,
`operator.py`, `pipeline.py`, `stats.py` — faqat router ulaydi.

Ya'ni: **"agent qachon odamsiz ishlay boshlaydi"** degan savolning javobi
(`LadderStore`) va **"write-harakat operator roziligisiz o'tmaydi"** degan
qoidaning saqlagichi (`FileApprovalStore`) butunlay **qizil va gatesiz** to'plamda
qadalgan edi. Bu §155 ning eng qimmatli satri edi; bu faza uni to'laydi.

### §156.2. Sanab chiqilgan chegaralar

| Modul | Chegara | Qiymat | Oldin |
|---|---|---|---|
| `ladder.py` | ko'tarilish ostonasi | 30 vazifa | konstruktor default'i |
| `ladder.py` | ko'tarilish xato ulushi | 0.05 | konstruktor default'i |
| `ladder.py` | tushish xato ulushi | 0.20 | konstruktor default'i |
| `ladder.py` | avtomatik shift | yoniq | konstruktor default'i |
| `ladder.py` | oyna poli | 30 | **nomsiz ikkinchi literal** |
| `ladder.py` | oyna ifodasi | `max(MIN_WINDOW, min_tasks)` | **nomsiz** |
| `ladder.py` | pog'ona tartibi | `LEVELS` | nomsiz |
| `approvals.py` | navbat shifti | 100 qator | default argument |
| `approvals.py` | sabab shifti | 200 belgi | **nomsiz literal** |
| `approvals.py` | id entropiyasi | 12 hex belgi | **nomsiz literal** |
| `approvals.py` | qaror lug'ati | 2 so'z | **ikki marta yozilgan** |
| `approvals.py` | telefon maskasi poli | 9 belgi | regex kvantifikatorida |
| `approvals.py` | telefon maskasi shifti | 16 belgi | regex kvantifikatorida |

### §156.3. `MIN_WINDOW` — **o'lik qoida**, va u hech narsa ko'tarmaydi

Bu fazaning eng muhim chegara topilmasi. `MIN_WINDOW` — `min_tasks` default'i ostida
turgan **ikkinchi yalang'och `30`**: mos kelishi shart bo'lgan ikki literal, va
ularni mos qiladigan hech narsa yo'q edi.

Ular **haqiqatan** mos kelishi shart, va sabab kosmetik emas:

```
record:    entry["outcomes"] = (entry.get("outcomes", []) + [bool(ok)])[-self.window:]
record:    if total >= self.min_tasks:
```

`record` tarixni `[-window:]` gacha qisqartiradi, ko'tarilish esa `total >= min_tasks`
talab qiladi. Ya'ni **`window < min_tasks` bo'lsa, `total` bu songa hech qachon
yetmaydi** — agent hech qachon ko'tarilmaydi. Bu **o'lik qoida**: u hech qachon
ishlamaydi va **hech qanday istisno ko'tarmaydi**. `max(MIN_WINDOW, min_tasks)`
savolni butunlay olib tashlaydi, va o'lchov poli ikki tomonida ham o'tkazildi:

| `min_tasks` | 1 | 5 | 29 | 30 | 31 | 100 |
|---|---|---|---|---|---|---|
| `window` | 30 | 30 | 30 | 30 | **31** | **100** |

### §156.4. Siyosat uchligi — bu sozlama emas, **javob**

`min_tasks`, `max_err`, `demote_err` — "agent qachon odamsiz ishlashni boshlaydi"
degan savolning javobi. Ular konstruktor default'i edi, ya'ni:

```python
LadderStore(min_tasks=1)   # bitta muvaffaqiyatli vazifadan keyin ko'taradi
```

...va **birorta testning rangi o'zgarmasdi**. Yagona signal — agentlar tezroq
avtonom bo'lib qolgani. Endi uchtasi ham matritsada qizil.

`auto_cap` ham shu yerda: `auto_cap=True` — avtomatik ko'tarilish faqat
`human_assisted` gacha, `autonomous` esa faqat owner endpoint'idan. Bu audit S29
qo'riqchisi (self-approve farming). Default'ni `False` qilish — agent o'z
write-harakatlarini o'zi tasdiqlay oladigan holat.

### §156.5. `DECISIONS` — lug'at ikki marta yozilgan, uchinchi nusxa ortiqcha edi

Qaror lug'ati (`approved` / `rejected`) ikki joyda yozilgan edi: store `ValueError`
ko'taradi, marshrut `422` qaytaradi, va **ikkisini mos qiladigan hech narsa yo'q**.
Uchinchi nusxa esa umuman ortiqcha edi:

```python
("approved" if decision == "approved" else "rejected", ...)
```

Yuqoridagi qo'riqchidan keyin bu shunchaki `decision`. Uchta yozuv o'rniga bitta
manba qoldi: `DECISIONS`, ikkala qo'riqchi joyda ham.

### §156.6. Telefon maskasi — ikki tomoni ham **sizib chiqadi**

`PHONE_MASK = \+998[\d\s\-()]{9,16}`. Kvantifikator PII chegarasi, va u **nom
bilan emas, xatti-harakat bilan** qadalgan: nomlash uchun pattern'ni f-string'dan
qurish kerak bo'lardi — o'qiladigan regex'ni hech kim o'qimaydigan nomga
almashtirish. Uni pol va shiftning bir belgi narisidan oziqlantirish aynan shu
gapni isbotlaydi. Va ikkala tomon ham **sizib chiqadi** — ikkisi ham o'lchandi va
testda **sizib chiqish sifatida** yozilgan:

| Tana belgilari | Natija |
|---|---|
| 8 | `+99890123456` — **maskalanmagan** |
| 9 | `+998***` |
| 16 | `+998***` |
| 17 | `+998***6` — **dumi qolgan** |

Ya'ni `+998` dan keyin to'qqiz belgidan **qisqa** raqam (qisqartirilgan yoki
to'liq terilmagan) operator ekraniga **o'z holida** chiqadi; o'n oltitadan
**uzuni** esa oxirini saqlab qoladi. Yana bir mayda iz: ajratgich yutiladi, shuning
uchun `call +998 90 123 45 67 now` → `call +998***now`.

Bu ikki chekka bu fazada **kiritilmagan** nuqson emas — ular allaqachon shu holatda
edi. Ular shu fazada **yozib qo'yildi**, chunki chegara endi nomlangan va uni
o'zgartirish qanday natija berishini bilish kerak.

### §156.7. O'lchov xatosi: `storage.reset()` faylni **o'chirmaydi**

Bu modulning dastlabki probe'i (`_probe156.py`) bir xil `APP_DB` da o'nlab
ssenariy yurgizdi va **siyosat deb oldingi ssenariyning qoldig'ini** o'qidi:

```
errs=0 -> human_assisted      <- to'g'ri
errs=1 -> human_assisted      <- oldingi ssenariydan meros
after 29 successes: human_assisted   <- 29 < 30, ko'tarilmasligi kerak edi
```

Sabab: `storage.reset()` faqat **ulanishni yopadi**, faylni qoldiradi. Har bir
testga alohida `APP_DB` berilgach raqamlar izchil bo'ldi:

```
29 successes -> human_led | 30 -> human_assisted
errs=1 (0.0333) -> human_assisted | errs=2 (0.0667) -> human_led
6/30 (==0.20) -> demoted emas | 7/30 (0.2333) -> bir pog'ona pastga
```

Bu §155.6 dagi bilan **bir sinf**: o'lchov to'g'ri ko'rinadi, lekin o'lchanayotgan
narsa boshqa. Farqi shundaki, u yerda sabab test to'plamida, bu yerda **mening
probe'imda** edi.

### §156.8. Natija — 38/38 qizil

| Modul | Mutatsiya | Natija |
|---|---|---|
| `app/ladder.py` | 19 | 19 RED |
| `app/approvals.py` | 19 | 19 RED |
| **Jami** | **38** | **38 RED, 0 GREEN, 0 o'lchanmagan** |

Har ikki modulda `CONTROL GREEN` va `restore verified: YES`; sidecar qolmadi.
Yangi to'plam `runtime_tests/test_approval_ladder_bounds.py` — **76 sinov**, yashil.

Chegara qadalgan joyning **o'zida** o'lchash uchun ikki sinov ataylab shunday
tanlandi: `min_tasks=30` da 5% **hech qachon butun songa tushmaydi** (1.5), shuning
uchun `<=` ni `<` dan ajratib bo'lmasdi. `min_tasks=20` esa tushiradi — bitta xato
yigirmada **aynan** 5%. `demote_err` uchun ayni shu narsa 6/30 = 0.20 bilan
bajariladi.

### §156.9. Halol cheklov: **mustaqil iste'molchi yo'q**

§155 da o'lchov spetsifikatsiyasi **ikki fayl** edi: `test_app_layer_bounds` (qadam)
va `test_identity_store` (iste'molchi — haqiqiy sessiya, taklifnoma, parol yaratadi).
Bu fazada **ikkinchi fayl yo'q**: `grep` boshqa iste'molchini topmaydi.

Shuning uchun bu yerda qadamlarning kuchi **boshqacha**, va uni yozib qo'yish kerak:
ular **xatti-harakat** bilan qadalgan — haqiqiy SQLite orqali haqiqiy store'ni
yurgizadi va haqiqiy qatorni qaytarib o'qiydi — lekin **mustaqil chaqiruvchi**
kengaytirilgan chegarani sezmaydi. Bu §155 dan **kuchsizroq**, va yashirmasdan
shunday yozildi.
### §156.10. `verify_offline.py` o'lchagan narsani aytmaydi: **164 xato, 12 emas**

Gate Python ishlarini `sys.executable` bilan yurgizadi — ya'ni uni **kim ishga
tushirgan** bo'lsa, o'sha interpreter bilan. Bu ataylab: muhit ham o'lchanayotgan
narsaning bir qismi. Lekin shu sababli **noto'g'ri ishga tushirish haqiqiy
nuqsondan farq qilmaydi**.

Men gate'ni boshqariladigan venv o'rniga yalang'och `python` (3.13.12, site-packages
yo'q) bilan yurgizdim:

| | To'g'ri venv | Yalang'och `python` |
|---|---|---|
| `Ran` | **2905** | 2905 |
| Signature | `failures=1, errors=12, skipped=1` | **`failures=2, errors=164, skipped=1`** |
| `python_runtime` | `FAIL` | `FAIL` |
| Chiqish kodi | `1` | `1` |

Ikkisi ham bir xil satr bosadi, ikkisi ham `1` bilan chiqadi. `summary.json` ularni
**ajratmaydi**.

164 xatoning sababi bitta: `ModuleNotFoundError: No module named 'fastapi'` —
`fastapi` import qiladigan **har bir modul umuman import bo'lmaydi**:

```
ERROR: test_app_layer_bounds (unittest.loader._FailedTest.test_app_layer_bounds)
ERROR: test_approval_ladder_bounds (unittest.loader._FailedTest.test_approval_ladder_bounds)
ERROR: test_control_plane_bounds (unittest.loader._FailedTest.test_control_plane_bounds)
ModuleNotFoundError: No module named 'fastapi'
```

Qolgan ~150 tasi `cryptography` yo'qligidan (`Secret encryption failed`).

**Eng achchiq joyi:** ro'yxat allaqachon bor edi — lekin **eng oxirida**,
`summary.json` ichida `http_dependencies_missing` deb hisoblanardi: uch daqiqalik
traceback'dan keyin, o'quvchi bilib izlashi kerak bo'lgan maydonda.

Tuzatish: ishlar boshlanishidan **oldin** rad etish, interpreter nomini aytib, va
dalil papkasi yaratilishidan **oldin** — shunda rad etish tugallangan yurishga
o'xshab qoladigan bo'sh papka qoldirmaydi.

**Ikki ro'yxat, va nega torrog'i to'g'ri.** Birinchi urinishda men `psycopg` va
`redis` ni ham blokladim — va gate **shu repozitoriyning bazaviy o'lchovi olingan
venv**ni rad etdi:

```
REFUSED: ...envs\default\Scripts\python.exe cannot import psycopg, redis.
```

Ular — baza drayverlari: yo'qligi bir nechta kontrakt testini **skip** qiladi,
to'plamning import bo'lishini to'xtatmaydi. Shuning uchun:

* `REQUIRED_DEPENDENCIES` — import uchun zarur, **bloklaydi**;
* `HTTP_DEPENDENCIES` — yuqoridagilar + ixtiyoriy drayverlar, `summary.json` da
  **xabar qilinadi**.

Va rad etish ochib bergan bo'shliq: **`cryptography` ikkala ro'yxatning hech
birida yo'q edi.** Uning yo'qligi 164 xatoning ko'p qismini berdi, `summary.json`
esa bu paketni **bir marta ham** nomlamagan bo'lardi. Endi u bloklovchi ro'yxatda.

### §156.11. Bo'sh papka — bu rad etishning isboti **emas**

§156.10 ni tuzatgandan keyin men `docs/verification/` ostidagi bo'sh papkani
"rad etilgan yurishning qoldig'i" deb o'qib **o'chirdim**. U qoldiq emas edi:
u **hozir yurgizilgan** gate'ning tirik dalil papkasi edi.

Gate papkani **birinchi** yaratadi, `*.log` fayllarni esa har bir ish
**tugagach** yozadi. Ya'ni `python_runtime` (uch daqiqa) ishlayotgan vaqtda papka
**bo'sh bo'lishi kerak**. Yurish birinchi log'ni yozmoqchi bo'lganda
`FileNotFoundError` bilan o'ldi.

Bu §155.6 bilan **bir sinf**: signal bir narsaning isboti deb o'qildi, aslida
boshqa narsaning isboti edi. Bo'sh papka "rad etilgan" bilan ham, "ishlayotgan"
bilan ham mos keladi; ikkisini faqat **jarayonlar jadvali** ajratadi.

Aynan shu xato qilinishiga sabab bo'lgan narsa ham bor edi: oldingi sessiyadan
`local-20260922T025813390316Z` degan **haqiqiy** bo'sh qoldiq qolgan edi. Endi
qo'riqchi `mkdir` dan **oldin** ishlagani uchun rad etish umuman papka
yaratmaydi, va bu noaniqlik yo'q.

## §157. Fazza ellik ikkinchi — Customer 360: **bir son ikki joyda, va qaysi biri tor bo'lsa jimgina o'sha yutadi**

### §157.1. Nega aynan `customer360.py`, va §156 ning cheklovi qanday o'lchandi

§155 `app/` qatlamini ochdi va bitta gapni qoldirdi: *o'qilmaydigan to'plamdagi qadam —
qadam emas*. §156 o'sha gapning manzilini topdi va eng og'ir holatini — gatesiz to'plamda
qadalgan ikki store'ni — yopdi. Lekin §156 **halol cheklov** bilan tugadi:

> `LadderStore` va `FileApprovalStore` ni gate ichida hech kim ishlatmaydi... **mustaqil
> chaqiruvchi** kengaytirilgan chegarani sezmaydi. Bu §155 dan **kuchsizroq**.

Bu fazada o'sha cheklov **o'lchanadigan** qilindi. `app/` qatlamida qolgan modullar ichida
eng ko'p chegarali fayl — `customer360.py`, va u §156 dan tub farq qiladi: uning **uchta
mustaqil iste'molchisi** bor.

| Iste'molchi | Nimani qadaydi |
|---|---|
| `runtime_tests/test_customer360.py` | tenant chegarasi, kanal identifikatorining boshqa customer'ga o'tmasligi, buyurtma idempotentligi, query SQL emasligi |
| `runtime_tests/test_identity_hardening.py` | buyurtma customer'lar orasida ko'chmasligi, muzlatilgan workspace yozuvni rad etishi |
| `runtime_tests/test_control_plane_authority.py` | tushirib qoldirilgan `actor` — imtiyozli xizmat identifikatori emas; bekor qilingan a'zo yoza olmasligi |

Ya'ni bu fazada "qadalgan" degan gap **"mustaqil chaqiruvchi sezadi"** degan ma'noni beradi.
Buning uchun matritsa **ikki marta** yurgiziladi (§157.10) — bir marta o'z modulim bilan, bir
marta faqat iste'molchilar bilan.

### §157.2. Sanab chiqilgan chegaralar

| Chegara | Qiymat | Oldin |
|---|---|---|
| `_text` poli / shifti | 1 / 256 | default argumentlar |
| identifikator shifti | 128 | **kelishishi kerak bo'lgan ikki literal** |
| tenant shifti | 64 | default argument |
| kontakt qiymati shifti | 512 | default argument |
| telefon raqami poli | 7 | **nomsiz literal** |
| `external_ref` shifti | 256 | **nomsiz literal** |
| sahifa `limit` poli / shifti | 1 / 100 | **nomsiz literal**, va xabarda ham |
| sahifa `offset` shifti | 100_000 | **nomsiz literal** |
| `query` shifti | 256 | **nomsiz literal** |
| ichki to'plam shifti | 100 | **to'rt alohida literal** |
| oldindan filtr shiftlari | 32 / 32 / 32 / 8 | **to'rt nomsiz literal** |
| buyurtma jamlanmasi shifti | 10**15 | **nomsiz literal** |
| buyurtma holati lug'ati | 6 so'z | **qo'riqchi ichida inline** |
| valyuta shakli | 3 harf | nomsiz regex |
| yozuv rollari | 4 dan 2 tasi | **qo'riqchi ichida inline** |
| `status!='deleted'` | 4 joy | **nomsiz, va API orqali yetib bo'lmaydi** |

### §157.3. Bir son, ikki joy — va qaysi biri tor bo'lsa jimgina o'sha yutadi

`_id` ikki qadamdan o'tadi:

```python
def _id(value: str, name: str = "id") -> str:
    value = _text(value, name, maximum=MAX_ID_CHARS)
    if not _ID_RE.fullmatch(value):
        raise CustomerError(f"{name} noto'g'ri")
    return value
```

va regex o'sha sonni **ikkinchi marta** yozgan edi:

```python
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
```

Ikki literal **mos kelishi shart**, lekin ularni mos qiladigan hech narsa yo'q edi. Va bu
yerda xato ko'rinmaydi: `{1,128}` ni `{1,12}` qilish **hech qanday xatolik ko'tarmaydi** —
identifikatorlar shunchaki qisqaradi, xato xabari esa `128` ni **bir marta ham** eslatmaydi.
Ya'ni tor tomon jimgina yutadi, ikkinchisi esa o'z sonini himoya qilib turadi.

Tuzatish — qadam qo'yish emas, **savolni yo'q qilish**:

```python
_ID_RE = re.compile(rf"^[A-Za-z0-9_-]{{1,{MAX_ID_CHARS}}}$")
```

Endi kelishib olishi kerak bo'lgan ikkinchi literal **yo'q**. Bu §156 dagi
`max(MIN_WINDOW, min_tasks)` bilan **aynan bir harakat**: ikki sonning mos kelishini test bilan
qadash o'rniga, ikkinchi sonni umuman yozmaslik.

### §157.4. Dominat qilingan shiftlar — to'plam emas, **sabab** qadaladi

`kind`, `channel`, `status` va `currency` uzunlikka tekshiriladi, **keyin darhol** kichik
lug'at yoki regex bo'yicha tekshiriladi:

```python
kind = _text(kind, "type", maximum=MAX_KIND_CHARS).casefold()
if kind not in _ALLOWED_CONTACT_TYPES:
    raise CustomerError("contact type noto'g'ri")
```

Ya'ni bu shiftlar **qabul qilinadigan to'plamni belgilamaydi** — ular faqat lug'atga qadar
bajariladigan ish hajmini chegaralaydi. `MAX_KIND_CHARS` ni 32 dan 16 ga tushirish ham, 64 ga
ko'tarish ham to'plamni o'zgartirmaydi; faqat **ish hajmi** o'zgaradi.

Shuning uchun ularni to'plam bilan qadab bo'lmaydi. Ular **sabab** bilan qadalanadi:

| Kirish | Rad etish sababi | Xabar |
|---|---|---|
| 33 belgili `kind` | **uzunlik** | `type uzunligi noto'g'ri` |
| 10 belgili `kind` | **lug'at** | `contact type noto'g'ri` |

Ikki xil sabab, ikki xil yo'l, bir xil istisno **turi**. Faqat `CustomerError` ni tekshirgan
test **ikkisini ham qadalamaydi** — u shunchaki "nimadir rad etildi" der. Bu §155.6 dagi
qoidaning uzunlik tekshiruviga qo'llangan shakli: *shart bajarilgani — sabab ekanining isboti
emas*.

### §157.5. Bitta shift, to'rt joy — va to'rttasi bir xil kuchda qadalmagan

`get_customer` to'rt to'plam qaytaradi va har biri `LIMIT 100` bilan chegaralangan edi —
**to'rt alohida literal**. Bittasini o'zgartirish qolgan uchtasini jimgina ortda qoldirardi.
Endi to'rttasi ham bitta konstantadan:

```python
f"... ORDER BY created LIMIT {MAX_EMBEDDED_ROWS}"
```

Lekin halollik shuni talab qiladi: **to'rttasi bir xil kuchda qadalmagan.** `contacts` va
`orders` to'plamlarini arzon to'ldirish mumkin (101 ta yozuv), shuning uchun ular
**xatti-harakat** bilan qadalgan — funksiya 100 ta qaytaradi. `channel_identities` va
`conversations` uchun bunday test yozilmagan; ular **manba sanovi** bilan qadalgan:

```python
self.assertEqual(4, text.count('LIMIT {MAX_EMBEDDED_ROWS}'))
```

Bu ham qadaladi — to'rt joydan birortasini literalga aylantirish sanovni 3 ga tushiradi — lekin
**boshqa turdagi** qadam: qaytarilgan ro'yxat emas, matn tekshiriladi. Farq yozib qo'yildi,
yashirilmadi.

### §157.6. Hech bir test yeta olmaydigan qo'riqchi: `status!='deleted'`

To'rt predikat `status!='deleted'` ni tekshiradi:

| Joy | Nima uchun |
|---|---|
| `get_customer` | qabr toshi o'qilmasin |
| `list_customers` | ro'yxatda ko'rinmasin |
| `_ensure_customer` | yangi bola qabul qilmasin |
| `create_customer` | `deleted` **o'rnatilmasin** |

Oxirgisi qolgan uchtasini **o'lchab bo'lmaydigan** qiladi: API orqali `status='deleted'` bilan
qator yaratib bo'lmaydi, ya'ni modul ichidan yurgan **hech bir test** o'sha qatorni yarata
olmaydi. To'rt qo'riqchi, **nol qoplama** — va to'rttasini o'chirish to'plamni yashil qoldirdi.

Bu "qo'riqchi faqat uni qaytarish testni qizartirsa qadalgan" qoidasining **teskarisi**: ba'zi
qo'riqchilarga API qabul qiladigan **hech bir kirish** yetib bormaydi, chunki boshqa qo'riqchi
birinchi bo'lib o'z ishini qilyapti.

Yechim — qatorni API **ostidan** ekish:

```python
row = create_customer(tenant, 'Gone', actor=self.actor)
db().execute("UPDATE p_customers SET status='deleted' WHERE id=?", (row['id'],))
db().commit()
```

va qo'riqchini o'qiladigan **uch joyning hammasida** o'lchash. API yarata olmaydigan qator —
bu baribir sxema, migratsiya yoki import yaratadigan qator.

### §157.7. `_writable` va e'lon qilinmagan subset

`identity_store.ROLES` to'rt rolni e'lon qiladi:

```python
ROLES = {'owner', 'operator', 'integrator', 'viewer'}
```

`_writable` esa yozish huquqini qo'riqchi **ichida** sanab o'tgan edi:

```python
if not m or m['role'] not in {'owner','operator'}: raise AuthenticationError('Write permission revoked')
```

Ikki literal, va **ikkisi ham** ikkinchisidan bexabar. Lug'atga rol qo'shilsa yoki qo'riqchi
xato bilan kengaytirilsa — **customer ma'lumotini kim yozishi** jimgina o'zgaradi. Hech bir
to'plamda **owner ham, operator ham bo'lmagan** a'zo yaratilmagan edi.

Muhim nuqta: bu **subset** munosabati, va uni inline literal ifodalay olmaydi. Endi nomlangan:

```python
WRITE_ROLES = frozenset({"owner", "operator"})
```

va munosabat **o'zi** tekshiriladi, ikki qiymat emas:

```python
self.assertLessEqual(set(customer360.WRITE_ROLES), set(identity.ROLES))
```

So'ng xatti-harakat **ikki tomonga** o'lchanadi — subset **tor** bo'lib ham buzilishi mumkin:
har bir e'lon qilingan rol uchun a'zo yaratiladi va o'qish-uchun rollar (`viewer`,
`integrator`) rad etilishi, yozuv rollari (`owner`, `operator`) esa o'tishi tekshiriladi.
A'zolik qatori SQL bilan ekiladi, chunki API faqat taklifnoma aylanishini taklif qiladi — va
o'lchanayotgan narsa rol, taklifnoma emas.

### §157.8. Chegaraning **uchinchi** joyi: chaqiruvchi o'qiydigan xabar

Sahifalash oynasi kodda **uch marta** yozilgan edi:

```python
MIN_PAGE_LIMIT = 1
MAX_PAGE_LIMIT = 100
...
if not isinstance(limit, int) or isinstance(limit, bool) or not MIN_PAGE_LIMIT <= limit <= MAX_PAGE_LIMIT:
    raise CustomerError("limit 1..100 bo'lishi kerak")      # <-- uchinchi joy
```

Xabarni **hech kim import qilmaydi**, shuning uchun uni **hech narsa** tutmaydi.
`MAX_PAGE_LIMIT` ni 200 ga ko'tarsangiz, qo'riqchi 200 ni qabul qiladi, chaqiruvchiga esa
"chegara 100" deb aytiladi — **to'liq ishonch bilan aytilgan noto'g'ri javob**.

Endi uchinchi joy ham konstantadan quriladi:

```python
raise CustomerError(f"limit {MIN_PAGE_LIMIT}..{MAX_PAGE_LIMIT} bo'lishi kerak")
```

va **ikki marta** qadalanadi: bir marta manbada, bir marta **istisnoda** — chunki chaqiruvchi
aslida ko'radigan narsa xabar:

```python
with self.assertRaises(CustomerError) as caught:
    list_customers('tt', limit=customer360.MAX_PAGE_LIMIT + 1)
self.assertIn(f'{customer360.MIN_PAGE_LIMIT}..{customer360.MAX_PAGE_LIMIT}',
              str(caught.exception))
```

Umumiy qoida: modulda sonning **har bir literal shaklini** qidiring — yalang'och raqam, `a..b`
oralig'i, odam tilidagi gap — faqat taqqoslashlarni emas.

### §157.9. Test bor, lekin u boshqa narsani o'lchaydi: `test_query_is_not_sql`

`test_customer360.py` da `query` bo'yicha test **bor**:

```python
def test_query_is_not_sql(self):
    create_customer('tt', 'Ali', actor=self.owner['id'])
    create_customer('tt', 'Vali', actor=self.owner['id'])
    result = list_customers('tt', query="' OR 1=1 --")
    self.assertEqual([], result)
```

Uning nomi "query — SQL emas", va bu **to'g'ri** — lekin u `escaped = query.replace(...)`
qatorining **birorta** almashtirishini o'lchamaydi, chunki qiymat allaqachon `?` bilan
**bog'langan**. `' OR 1=1 --` da `%`, `_` ham, `\` ham yo'q.

Ya'ni bu test uchta almashtirishning **uchtasini ham** qoldirib, yashil qolaveradi. Ular
boshqa narsaga qarshi: `LIKE` naqshining **joker belgilariga**. Uchala almashtirishning o'z
testi shu fazada yozildi:

| Almashtirish | Nima uchun | Usiz nima bo'lardi |
|---|---|---|
| `.replace("%", "\\%")` | `%` — naqshda "hamma narsa" | `%` qidiruvi **butun ro'yxatni** qaytarardi |
| `.replace("_", "\\_")` | `_` — naqshda "bitta belgi" | `a_b` qidiruvi `axb` ni ham topardi |
| `.replace("\\", "\\\\")` | `ESCAPE '\'` dan keyin teskari chiziq **ma'noga ega** | `a\b` qidiruvi `ab` ni topardi |

Uchinchisi eng oson ko'rilmaydigani: u **hech qayerda** yozilmagan, va usiz `a\b` naqshi
`a` + "b harfining o'zi" bo'lib qoladi — ya'ni qidiruv **so'ralmagan narsani** topadi.

Bu §157.9 ning umumiy darsi: **testning nomi uning o'lchovini aytmaydi.** `test_query_is_not_sql`
SQL in'ektsiyasini o'lchaydi — va SQL in'ektsiyasi bu yerda `?` tufayli **umuman mumkin emas** —
escapingni esa o'lchamaydi.

### §157.10. Birinchi yurish: 60 ta mutatsiyadan **4 tasi yashil** — va ularning uchtasi bir oila

Matritsa ikki yurishda yurgizildi (§157.1): **pass A** — faqat `test_customer360_bounds`
(51 mutatsiya), **pass B** — faqat uchta mustaqil iste'molchi (9 mutatsiya). Birinchi yurish:

| Yurish | Mutatsiya | RED | GREEN | O'lchanmagan |
|---|---|---|---|---|
| A (`test_customer360_bounds`) | 51 | 49 | **2** | 0 |
| B (iste'molchilar) | 9 | 7 | **2** | 0 |

Har ikkala yurishda `CONTROL GREEN`, `restore verified: YES`, sidecar qolmadi. To'rt GREEN:

**1. `channel identity needs a real true`** (`if verified is not True:` → `if False:`) —
**noto'g'ri yurishga yozilgan mutatsiya**, kod nuqsoni emas. Uni `test_customer360.py` dagi
`test_channel_identity_requires_explicit_verification` qadaydi — pass B dagi test. Men uni
pass A ga yozgan edim; pass A esa uni o'lchamaydi. Yechim: mutatsiyani pass B ga ko'chirish.
Bu §157.10 ning eng arzimagan topilmasi — lekin u ikki yurishli dizayn **o'zini tekshiradi**
degan isboti.

**2. `a tombstone takes no new children`** (`_ensure_customer` predikati) — **haqiqiy nuqson,
va sababi §155.6 ning o'zi.** Testim faqat `CustomerNotFound` turini tekshirardi. Lekin har bir
mutator oxirida `get_customer` ni qaytaradi — va U qabr toshida ham ko'taradi, **bola
yozilib bo'lingandan keyin**. Predikatni o'chirish INSERT'ga yo'l ochadi, keyin o'qib
qaytarish xatolik beradi — va testim **xuddi shu istisnoni** ko'rib, yashil qolardi. Yechim:
qadamni istisno **turi** bilan emas, **natija** bilan qadash — rad etilgan chaqiruvdan keyin
**qator yozilmaganini** tekshirish (`count == 0`).

**3. `the write path needs an actor`** (`if False:raise`) — **2-bandning akasi.** Bo'sh
`actor` hali ham rad etiladi — chunki keyingi qator (`_membership`) `None` qaytaradi va
"Write permission revoked" ko'taradi. Ya'ni qo'riqchi o'sha vazifani keyingi qator bilan
bo'lajadi — lekin **xabar o'zgaradi**: "Write actor required" → "Write permission revoked".
Bu ikki xil audit izi: bo'sh actor — kod nuqsoni, noto'g'ri actor — ruxsat masalasi. Yechim:
**xabar bilan** qadash.

**4. `the write path requires the customer to exist`** (`_ensure_customer` o'chirilishi) —
**2-band bilan bir xil maskirovka.** `get_customer` o'qib qaytarishda ko'taradi, lekin
**jetim qator** allaqachon yozilgan — boshqa customer'ga emas, umuman **yo'q** customerga
ishora qiluvchi kontakt qatori. Yechim: yo'q bo'lgan customer'ga kontakt qo'shilgach **qator
yozilmaganini** tekshirish.

To'rtdan uchtasi (2, 3, 4) — bitta oilaning bolalari, va u bu fazaning eng qimmatli darsi:

> **Istisno turi — rad etishning isboti emas.** Har bir mutator oxirida `get_customer` ni
> qaytaradi, shuning uchun qo'riqchi o'chirilganda ham chaqiruv "rad etiladi" — faqat **bir
> qator keyinroq**, va yozuv allaqachon bo'lib bo'lgan. `assertRaises(X)` bu ikkisini
> ajratmaydi. Yozuv qo'riqchisini faqat **yozilmagan qator** bilan qadash mumkin.

### §157.11. Ikkinchi yurish: **60/60 RED**

To'rt GREEN uchun to'rt tuzatishdan keyin matritsa qayta yurgizildi:

| Yurish | Mutatsiya | RED | GREEN | O'lchanmagan |
|---|---|---|---|---|
| A (`test_customer360_bounds`) | 52 | **52** | 0 | 0 |
| B (iste'molchilar) | 8 | **8** | 0 | 0 |
| **Jami** | **60** | **60** | **0** | **0** |

Har ikkala yurishda `CONTROL GREEN`, `restore verified: YES`, sidecar qolmadi. Yangi
to'plam — `runtime_tests/test_customer360_bounds.py` — **72 sinov** (77 subtest), yashil.

### §157.12. §156 dan farqi: endi mustaqil chaqiruvchi **bor**

§156 o'zining eng zaif gapini shunday yozgan edi: qadamlar xatti-harakat bilan o'lchangan,
lekin **mustaqil chaqiruvchi** kengaytirilgan chegarani sezmaydi.

Bu fazada o'sha bo'shliq **yozilmagan, o'lchangan**: matritsaning **ikkinchi yurishi** —
faqat uchta iste'molchi, o'z modulim **chiqarib tashlangan** holda. Uning 8 mutatsiyasining
har biri RED:

| Mutatsiya | Uni qadagan test |
|---|---|
| ro'yxat tenant'ga bog'langan | `test_customer_360_is_tenant_scoped` |
| detalni o'qish tenant'ga bog'langan | `test_customer_360_is_tenant_scoped` |
| kanal identifikatori boshqa customer'ga o'tmaydi | `test_channel_identity_never_cross_customer_merges` |
| buyurtma customer'lar orasida ko'chmaydi | `test_customer_order_cannot_move_between_customers` |
| kanal identifikatori haqiqiy `true` talab qiladi | `test_channel_identity_requires_explicit_verification` |
| yozuv uchun jonli rol kerak | `test_revoked_actor_cannot_write_customer` |
| muzlatilgan workspace yozuvni rad etadi | `test_freeze_rejects_customer_mutations` |
| takrorlangan buyurtma **yangilaydi** | `test_duplicate_external_order_is_idempotent_update` |

Ya'ni bu chegaralarni **kodni ishlatadigan boshqa odam** sezadi — §156 ning gapidan
kuchliroq, va bu marta yolg'iz modulning o'z-o'zini himoyasi emas.

### §157.13. Halol cheklovlar

- **To'plam to'liq yashil emas** — Windows'da 13 ta test **platforma** sababidan bloklangan
  (`privateFile` POSIX semantikasi; §154). Bu kod nuqsoni emas, va bu faza ularni
  ko'paytirmadi ham, kamaytirmadi.
- **`channel_identities` va `conversations`** to'plamlari faqat **manba sanovi** bilan
  qadalgan (§157.5) — qaytarilgan ro'yxat bilan emas. To'rt joyning to'rttasi ham RED,
  lekin ikkitasi matn tekshiruvi orqali.
- **Matritsa sekin** — 60 mutatsiya × ~30–70 soniya. Ikki yurishga bo'lish uni to'lanadigan
  qildi, lekin bu baribir o'nlab daqiqa.

# Audit tuzatishi, 2026-09-14

Oldingi mustaqil auditda `/runner/ws` exposed deb aytilgani noto‘g‘ri. v0.3.6 `main.py` legacy routerni import qilgan, lekin **include_router qilmagan**. Demak eski websocket applicationga ulanmagan. v0.3.7 foydalanilmaydigan importni olib tashlaydi, routerning qayta ulanmasligi uchun regression yozadi. Bu yangi auth bypass fix emas, noto‘g‘ri audit xulosasini tuzatish.

CORS GET/POST bilan cheklangani va yangi UI PUT yuborishi haqiqiy source nomuvofiqligi edi; allowlist moslashtirildi. Haqiqiy ASGI/browser tekshiruvi dependency-backed integration suite sifatida yoziladi, bu muhitda bajarildi deb ko‘rsatilmaydi.

## Audit, 2026-09-20 — P8/P8b/P8c ni mavjud logicga qarshi qayta tekshirish

Adversarial audit (to‘liq: `V05-BLOCKS-MESSAGING-UZ.md` §2). Uchta **jiddiy** nuqson topildi, hammasi o‘zim kiritgan va hammasi jimgina edi:

1. **Sana parseri standart ISO formatni rad etardi** (`2026-09-20T10:24:50.859388+05:00` → `None`), ya’ni oyna **yopiq** o‘qilardi va hozir yozgan mijozga javob berish rad etilardi. Regex o‘zi qo‘riqlayotgan `datetime.fromisoformat` dan qattiqroq edi. Tuzatildi + 2 regressiya testi.
2. **Test fixture’lari durotka o‘rnatilgan edi** (`ISO_OFFSET = 2026-09-19T10:00+05:00`), shuning uchun 24 soatdan keyin to‘plam **chirib** yiqilardi. Bu audit aynan o‘sha kuni o‘tkazildi va yiqilishni **mening o‘zgarishlarim** deb o‘yladim — keyin aslida vaqt bombasi ekani isbotlandi. Parsing va oyna fixture’lari ajratildi.
3. **`window_until` yoziladi, lekin o‘qilmaydi**: `engine.OUTBOUND_CHANNELS` da `whatsapp` yo‘q, shuning uchun kiruvchi oqim chiqish darvozasiga ulanmagan. Qayd etildi, ataylab ochiq (pretsedent qarori talab qiladi).

Yana sakkizta kichik/o‘rta nuqson (o‘lik `MAX_BODY_BYTES`, o‘lik `secret()` shoxobchasi, ixtiyoriy argumentning majburiy bo‘lishi — **P8b dagi aynan o‘sha xatoning takrori**, mavjud bo‘lmagan `created` ustuni, registrga sezgir prefiks, noaniq raqamning jimgina hal qilinishi, o‘lik konstantalar, vakuum probe) tuzatildi.

**Eng muhim saboq:** bir blokda o‘rganilgan saboq keyingi blokka avtomatik o‘tmaydi — P8b da topilgan sxema xatosi P8c da takrorlandi.

## Audit, 2026-09-20 — P8d ni o'z ichida tekshirish

P8d (`whatsapp_window_from_inbound_events`) yuqoridagi 3-topilmani **yopdi**, lekin o'z ichida **aynan o'sha sinfdagi** yangi nuqson chiqardi:

**`window_until` ikki marta hisoblandi.** `whatsapp_inbound` `window_until` ni **tugash vaqti** sifatida yozadi (Meta timestamp + 24 soat). `window_state(last_inbound)` esa **oxirgi kiruvchi vaqt** ni oladi va 24 soatni **o'zi** qo'shadi. Ikkalasini to'g'ridan-to'g'ri ulash 24 soatni **ikki marta** qo'shadi: **25 soat oldin** yozgan mijoz oyna **ochiq** deb o'qiladi — bu aynan modul oldini olish uchun qurilgan **131047** holati.

**Uni testlar emas, probe tutdi.** Bu uchinchi blokda uchinchi marta takrorlangan naqsh: `test_whatsapp.py` ning 119 testi ham, `test_whatsapp_inbound.py` ning 70 testi ham yashil edi, chunki ularning ikkalasi ham **o'z** tasviriga nisbatan izchil edi. Ikkalasini bog'laydigan **konversiya** hech qayerda tekshirilmagan edi. Tuzatish: `float(stamp) - WINDOW_SECONDS`, plus `test_the_event_window_is_not_double_counted` — u konversiyasiz shaklga qarshi **yiqiladi**.

**Pretsedent qoidasi qabul qilindi:** tasdiqlangan kiruvchi hodisa operator jadvalidan **ustun**, «yangiroq bo'lgani» emas. Sabab: qo'lda tahrirlangan jadval Meta o'zi bergan timestamp yopgan oynani **qayta ochmasligi** kerak.

**Ataylab qilinmagan ish:** `whatsapp` `OUTBOUND_CHANNELS` ga **qo'shilmadi**. Qo'shish `whatsapp.send` ni allowlist shoxobchasidan hodisa-bog'lash shoxobchasiga **ko'chiradi** — bu ruxsat qo'shish emas, **qoida almashtirish**, va oynadan tashqarida qonuniy yuboriladigan shablonlarni bog'lanadigan hodisasiz qoldiradi.

## Audit, 2026-09-20 — PRD §2.8 ni kodga qarshi tekshirish (P8L)

PRD §2.8 oyna holatini Business Graph'ning `customer` entity'siga `wa_window_until` atributi sifatida qo'shishni so'raydi. Bu **rad etildi**, lekin "qilmadik" va "qila olmadik" hisobotda bir xil ko'rinadi — shuning uchun sabab **o'lchandi** (`scripts/probe_wa_window_graph_attribute.py`, `PROVEN`; hisobot `V05-BLOCKS-MESSAGING-UZ.md`).

**Mexanik sabab:** grafda atribut — bu `row.get(field)`, ya'ni **ko'chirilgan katak**, hisoblangan qiymat emas. Shuning uchun `wa_window_until` deklaratsiyasi oynani **inson qo'lda yozadigan** ustunga bog'lardi, va u mijoz yozganda o'zi **yangilanmaydi**. Probe buni ko'rsatdi: 2020-yilda tugagan oyna `complete=true`, `source_errors=[]` bilan qaytadi — graf eski katakni joriydan **ajrata olmaydi**.

**Bu P8c ↔ P8d nuqsonining uchinchi ko'rinishi.** Uch blokda bir xil sinf: ikki modul bir xil tushunchani **ikki xil** ifodalaydi, ular orasidagi konversiya esa hech qayerda tekshirilmaydi:

1. P8c: `window_until` yoziladi, o'qilmaydi (manba uzilgan);
2. P8d: `window_until` (tugash) va `window_state` (oxirgi kiruvchi) aralashadi — 24 soat **ikki marta**;
3. P8L: oyna yana **qo'lda yuritiladigan** manbaga ko'chirilardi.

**Rad etish uch qismli:** atribut rad etildi; `observed` dan hisoblash rad etildi (`observed` — o'qish vaqti, yangilanish vaqti emas; qoralama va yuborish ikki xil surat o'qib kelishib qolmasdi); `whatsapp.window` ni graf manbasi qilish ham rad etildi (`SAFE_SOURCE_TOOLS` qator qaytaruvchi tool'lar bilan cheklangan, `entity_id` bog'lanmaydi, chiqish shakli mos emas, uchta ruxsat kerak bo'lardi).

**O'rniga qo'riqchi:** 15 test (`WaLifecycleGuardTests`) — kelajakda kim faqat PRD ni o'qib atributni qo'shsa, **yiqiladi**.

**Saboq:** PRD talabni to'g'ri qo'ygan, **usulni** noto'g'ri taklif qilgan. Talabni bajarish uchun taklif qilingan usulni **ko'r-ko'rona** bajarmaslik kerak — lekin rad etish ham **dalil** talab qiladi, aks holda u shunchaki e'tiborsizlik bo'lib ko'rinadi.

## Audit, 2026-09-20 — ERP posting blokini o'z ichida tekshirish (P8e)

Bu — repozitoriydagi **eng xavfli kod**: birinchi marta platforma **moliyaviy tizimga** yozadi. Shuning uchun audit ham odatdagidan qattiqroq bo'ldi va **oltita haqiqiy nuqson** topildi. Hammasi yozilgan paytda "to'g'ri" ko'rinardi.

**1. `_date_text('2026-13-01')` qabul qilinardi.** ISO shakli faqat regex bilan tekshirilardi — `2026-13-01` shaklga mos keladi, lekin **oy 13 emas**. Noto'g'ri davrga yozilgan hujjat — soliq muammosi. Tuzatish: `_real_date()` `calendar.monthrange` bilan.

**2. `failed` qator o'z retry'sini to'sardi.** `UNIQUE` indeks `(tenant,driver,kind,supplier,number)` ustida turgan edi, ya'ni bitta transport uzilishi qonuniy schyot-fakturani **abadiy** yozilmas qilardi va yagona chora — bazani qo'lda tahrirlash. Tuzatish: `claim_key` ustuni faqat `CLAIMING_STATUSES = {'posted','skipped_existing'}` uchun to'ldiriladi; `NULL` lar SQLite'da bir-biriga teng emas, shuning uchun `failed`/`unconfirmed` qatorlar yonma-yon yashay oladi.

**3. `posted()` `failed` qatorni qaytarardi**, shuning uchun 2-tuzatishdan keyin ham retry rad etilardi. Tuzatish: `AND status IN (CLAIMING_STATUSES)`.

**4. O'z `PATH_RE` im `/a/../b` va `//evil.example` ni qabul qilardi.** Men yo'l-gate'ini **qaytadan yozdim** — allaqachon mavjud, allaqachon testdan o'tgan `safe_relative_path` o'rniga. Takrorlash jarayonida **boshqacha** qaror qabul qildim. Tuzatish: `PATH_RE` o'chirildi, `_clean_path` `safe_relative_path` ga topshiradi. **Saboq:** *bir yo'l-gate'ining ikkinchi implementatsiyasi — uni xato qilishning ikkinchi imkoniyati.*

**5. Yetishmayotgan credential yutilardi.** `find_posted` uni "could not ask the ERP (RuntimeError)" ga o'rardı — bu operatorni **ERP'ni tekshirishga** yuboradi, holbuki tuzatish — **env var**. Tuzatish: `RuntimeError`/`ValueError` **aynan** qayta ko'tariladi, chunki xabarda o'zgaruvchining nomi bo'lishi kerak.

**6. Buzuq maydon "yetishmayotgan" deb xabar berilardi.** `total_minor: 1250000.5` — `missing` ro'yxatiga qo'shilardi, ya'ni xato **bilyard ostiga** yashirilardi. Tuzatish: absent → `missing` ro'yxati; malformed → darhol o'z sababi bilan raise.

**Audit tutgan bo'shliq (nuqson emas, qamrov):** foydalanuvchi "ikkalasi ham" degan edi, lekin birinchi versiyada faqat `onec_http` mashq qilingan edi. `custom_http` qo'shildi va uning **yagona** xulq-atvor farqi o'lchandi: flat `id` vs `result.Ref_Key`. Driver hech narsani o'zgartirmasa, custom ERP **o'zida bor** hujjatni ko'rmay qolardi — ikki marta yozish sharti.

**Yangi test yozishda o'zim qilgan xatolar** (kod emas, test): `identity()` tuple qaytaradi, men `['driver']` deb indeksladim; ikkinchi `register_erp_tools` chaqiruvidan `ValueError` kutdim, holbuki mavjud qoida "ikki marta registratsiya hech narsa qo'shmaydi"; `dict(os.environ, clear=True)` mock'i `PLATFORM_INTEGRATIONS_FILE` ni ham o'chirib tashladi; va `ScriptTransport` ning FIFO `pop(0)` semantikasi tufayli GET/POST javoblari almashib ketdi — shuning uchun `post_response` qo'shildi.
## Audit, 2026-09-20 — kechikkan ish eskalatsiyasini o'z ichida tekshirish (P6)

Bu blok **odamlar** haqida: uni noto'g'ri yozish xodimni jazolash vositasiga aylanishi mumkin. Shuning uchun ikki chegara (baholamaslik, ishga tegmaslik) **strukturaviy** qilindi, va audit **ikkita haqiqiy nuqson** topdi.

**1. `sent` qator cooldown o'tgach **qayta yuborilardi** — modulning **o'z docstring'iga zid**.** `_claim` `sent` va `failed` ni bir xil ko'rib chiqardi: cooldown o'tishi bilan ikkalasi ham qayta urinilardi. Natijada **o'sha kechikkan vazifa har kuni qayta eskalatsiya qilinardi** — menejer "Faktura #12 kechikdi" xabarini har kuni olardi. Docstring esa aksiyatni va'da qilardi ("A sent row is a decision already taken; it must not be re-attempted"). Bu **testlar emas, probe** tutgan (`a delivered item is never re-sent after the cooldown` → FAIL). Tuzatish: `sent` — bu kalit uchun **umrbod terminal**; faqat `queued` (crash) va `failed` (cooldown o'tgach) qayta uriniladi. Regressiya testi qo'shildi. **Saboq:** *docstring — dalil emas; xossani o'lchash kerak.*

**2. Provider uzilishi **tutilmagan istisno** edi.** `_read_overdue` `except (Conflict, ValueError, LookupError)` bilan o'ralgan edi, lekin Sheets transport nosozligi **`SheetsError`** ko'taradi — u `RuntimeError` avlodidir. Ya'ni har qanday o'qish uzilishi sikl ichida **tutilmagan istisno** bo'lib chiqardi: `escalation.cycle_failed` audit yozilmasdan, `tick()` yiqilardi — ya'ni "o'qilmadi" holati **jimgina yo'qolardi**. Tuzatish: o'qish ataylab `except Exception` bilan o'raladi; **faqat klass nomi** yoziladi (`type(error).__name__`), chunki provider xabari range/id/token aks ettirishi mumkin. Sabab: har qanday "o'qish bo'lmadi" holatida javob bir xil bo'lishi kerak — xabar yuborilmaydi, audit yoziladi, sikl qayta rejalashtiriladi.

**Probe'ning o'zida topilgan uchta xato (kod emas, probe):** probe qotib turgan soat ishlatardi, shuning uchun cooldown gate'ni **bilmasdan** bloklardi; `day_offset(-1)` fixturasi 5-bo'lim allaqachon eskalatsiya qilgan **aynan o'sha** kalitga to'g'ri keldi; va `_bounded(1, 'max_per_cycle')` salbiy tekshiruvi aslida **to'g'ri** qiymat edi (`LIMITS` = 1..50). Har biri tuzatildi — va ular ko'rsatadi: *probe ham noto'g'ri yozilishi mumkin, shuning uchun u o'z natijasini asoslashi kerak.*


## Audit, 2026-09-20 — ishlab chiqarish o'qishlarini o'z ichida tekshirish (P12)

Bu blok **rad etishlar** ustida qurilgan: OEE yo'q, chiqim bitta sondan hisoblanmaydi, xodim bo'yicha ko'rsatkich yo'q. Shuning uchun auditning asosiy savoli — **rad etish haqiqatan kodda bormi, yoki faqat docstringda?** Javob: ikki joyda **faqat docstringda** edi, va ikkalasini ham **probe** tutdi.

**1. Bo'sh chiqim katagi **uydirma `output: 0.0` stansiya** yaratardi — modulning **o'z docstring'iga zid**.** `yield_report` da `setdefault` **o'qiladiganlik tekshiruvidan oldin** ishlardi:

```python
output = _number(output_raw)
if _text(output_raw, 32).strip() and output is None:   # bo'sh '' bu yerga TUSMAYDI
    unreadable_out += 1
    continue
record = by_station.setdefault(name, {...'output': 0.0...})   # <-- shu yerda yaratilardi
```

Bo'sh katak `''` → `_number('')` = `None`, lekin `''.strip()` **bo'sh**, ya'ni guard ishlamaydi va `setdefault` **0.0 bilan yozuv yaratadi**. Natijada "liniya hech narsa ishlab chiqarmadi" degan **uydirma** raqam chiqadi. Docstring esa buning **teskarisini** va'da qilardi: *"a blank cell that becomes zero would understate output and inflate a yield"*. Probe o'lchadi (`a register of blanks produced no zero-output station` → FAIL, 2 ta uydirma), testlar tutmadi. Tuzatish: `output is None` bo'lsa **umuman yozuv yaratilmaydi**; bo'sh katak ("yozilmagan") va matnli katak ("o'qilmagan") **alohida** sanaladi, chunki menejer uchun ular boshqa gap — lekin **hech biri nol bo'lmaydi**.

**2. O'qilmaydigan nuqson katagi **butun qatorning chiqimini** summasidan yo'qotardi.** Nuqson tarmog'ida `continue` **yozuv yaratilgandan keyin** turardi:

```python
if defect is None and _text(defect_raw, 32).strip():
    unreadable_defect += 1
    continue        # <-- shu qatorning O'QILGAN chiqimi raqq orasida tashlanadi
```

Ya'ni bitta katakdagi matn stansiyaning o'z ishlab chiqarishini **jimgina yo'qotardi**. Tuzatish: o'qilmaydigan nuqson katagi chiqimni **yo'qotmaydi**; u stansiyani `defect_unreadable` deb belgilaydi va bu **butun stansiyani hisoblanmaydigan** qiladi. Sabab muhim: o'qiladigan kataklarni yig'ib, o'qilmaydiganini e'tiborsiz qoldirish nuqsonni **kam** ko'rsatib, chiqimni **shishirardi** — ya'ni 100% ga yaqin yolg'on.

**3. `path` uchun `or ''` — "joyi yo'q" degan ma'noni **o'qiladigan** qilardi (o'zim tutdim, kod yozish paytida).** Bog'lanmagan stansiya `'path': _bind(...) or ''` ko'rsatardi, ya'ni bo'sh satrni "bu stansiyaning **joyi yo'q**" deb o'qish mumkin edi — holbuki bu **ikki xil** gap. Tuzatish: `path` **faqat haqiqatan bog'langanda** qo'shiladi, va `unbound` soni buni **ko'rinadigan** qiladi. Bu P11b qoidasi: *"an event bound to the wrong plant is worse than an unbound one a manager can see."*

**4. `authority` bloki chiqishda **yo'q** edi (testlar tutdi, bu safar).** Butun oila har bir o'qishda qaysi vakolat ostida bajarilganini chiqishga qo'yadi; bu modul unutgan edi. Raqam **kim** tomonidan o'qilganini aytmasa, keyin audit qilib bo'lmaydi. Tuzatish: `_authority()` uchta view'ga ham qo'shildi.

**5. `_yield_tool` da indentation noto'g'ri edi** (loyiha uslubidan chetga chiqqan uzluksiz qator) — funksional emas, lekin keyingi muharrir uchun chalg'ituvchi. Tuzatildi.

**Probe'ning o'zida topilgan bitta xato (kod emas, probe):** 5-bo'lim "zavod-1 ning o'z stansiyasi 100 ko'rsatadi" deb kutgan edi, lekin fixture'da zavod-1 ning **ikkita** stansiyasi bor (100 va 200). Bu **probe xatosi**, modul xatosi emas — va tuzatishda **kutilmagan holat** ham qo'shildi: endi zavod-10 ning 50'si zavod-1 totallarida **yo'qligi** alohida o'lchanadi, ya'ni segment qoidasi **ikki tomondan** tekshiriladi.

**Saboq yana bir bor:** ikkita nuqson topildi va ikkalasi ham modulning **o'z docstring'iga zid** edi. Ya'ni *docstring — dalil emas; xossani o'lchash kerak.* Bu loyihaning takrorlanuvchi darsi, va bu blok u **sakkizinchi marta** tasdiqlandi.


## Audit, 2026-09-20 — OEE va andonni o'z ichida tekshirish (P13)

Bu blok P12 ning **topshirig'ini bajaradi** — lekin faqat mijoz **deklaratsiya qilgan me'yorlar** bo'yicha. Auditning asosiy savoli: **uchta faktor haqiqatan faqat deklaratsiya qilingan kirishlardan hisoblanadimi, yo'qmi — yoki birorta maxraj o'ylab topilganmi?** Va ikkinchi savol: **hujjat kod aytgan narsani aytadimi?**

**1. `_REGISTER_KEYS` da `planned_run_seconds` **yo'q** edi — konstanta funksiyasi **umuman yetib bo'lmas** edi.** Validator `ValueError: oee.registers line has unsupported keys: ['planned_run_seconds']` berardi. Ya'ni kodda mavjud bo'lgan butun bir imkoniyat (rejani stansiya bo'yicha konstanta sifatida deklaratsiya qilish — kichik zavodlar rejani aynan shunday biladi: "har smena 8 soat") **hech qachon ishlamasdi**. Tuzatildi, va config misolida endi `press` registri **aynan shu yo'lni** ishlatadi (checker buni `constants >= 1` bilan **majburan** tekshiradi).

**2. `good > produced` **347% OEE** berardi.** `_factor_quality` faqat `produced <= 0` ni rad etardi. `good = 9999`, `produced = 2400` → `quality = 4.1662` → OEE **3.4719**. Bu modulning **o'z sarlavhasiga zid** edi, va P12 dagi bilan **aynan bir sinf**: bayonot ishlatilgan, o'lchov emas. Tuzatish: `_factor_quality` endi `good > produced` ni **rad etadi**, va `report` buni `good_exceeds_produced` hisoblagichi orqali **ko'rinadigan** qiladi. Yana bir muhim ajratish: `produced: 0` — bu **`absent` emas, `unusable`**; kiritma **bor**, faqat nisbatning ma'nosi yo'q. Operatorni "yo'q ustun" ni qidirishga yuborish uni **mavjud** ustundan uzoqlashtirardi.

**3. Hujjat **kodga zid** edi — va bu safar nuqson kodda emas edi.** Sarlavha va `planned_run_seconds` hujjati shunday da'vo qilardi:

> *"Declaring both is a **refusal**, not a precedence rule: two sources for one denominator is an ambiguity, and the platform will not pick one silently."*

O'lchov buning **aksini** ko'rsatdi. Ustun + konstanta birga deklaratsiya qilinganda:

| Katak | Natija |
|---|---|
| `28800` | `planned = 28800` (ustun) |
| `10000` | `planned = 10000`, availability `2.6` (ustun ustunlik qiladi) |
| **bo'sh** | `planned = 28800` (konstantadan **fallback**) |

Ya'ni haqiqiy qoida — **ustun ustunlik qiladi, konstanta bo'sh katak uchun zaxira** — bu **mantiqiy dizayn**, va kichik zavod uni aynan shunday ishlatadi. Lekin hujjat uning aksini aytardi, va men **o'zim yozgan** config misoli, checker hamda (ikki joyda) test ham o'sha noto'g'ri da'voni kodlagan edi. **Kodni "tuzatish" noto'g'ri bo'lardi** — chunki kod to'g'ri edi. Tuzatildi: sarlavhaga o'lchangan qoida bo'limi qo'shildi, kod izohi va config misoli tuzatildi, checker endi **rad etishni emas, qabul qilinishini** o'lchaydi, probe **uchta** holatni (ustun ustunligi / bo'sh katak fallback / matnli katak) alohida o'lchaydi, va uchta yangi test qo'shildi. **Rad etish chegarasi aniq va o'zgarmadi:** rad etish — **ikkita registr** bir o'qish uchun deklaratsiya qilinganda.

**4. Bo'sh reja katagi konstantaga **tushmasdi** (testlar tutdi).** Konstanta fallback faqat `elif` tarmog'ida ishlardi, shuning uchun ustun deklaratsiya qilinib katak **bo'sh** bo'lsa — fallback ishlamasdi va availability **rad etilardi**, holbuki konstanta **aynan shu holat uchun** deklaratsiya qilingan edi. Tuzatildi: bo'sh katak standartga **tushadi**, lekin **matnli** katak baribir **sanaladi** (mijoz u yerga **biror narsa** yozgan, va uni jimgina konstanta bilan almashtirish mijoz deklaratsiya qilmagan rejani hisobot qilardi).

**Probe'ning o'zida topilgan ikki xato (kod emas, probe):** (a) 2-bo'lim performance tekshiruvi chegarani noto'g'ri o'qigan edi — `produced=100`, `run=26000`, `ideal=10` uchun `10*100/26000 = 0.0384615`, ya'ni modulning **0.0385** qiymati **to'g'ri** edi; probe tenglikni o'lchaydigan qilib tuzatildi. (b) 4-bo'lim fixture'da `oee` **va** `availability` chegarasini deklaratsiya qilgan edi, shuning uchun **ikkita** buzilish chiqdi; probe "1 kutilgan" deb o'lchardi — probe xatosi. Tuzatildi: endi bitta metrika **izolyatsiyada** bitta, ikkitasi **ikkita** buzilish chiqarishi **alohida** o'lchanadi — ya'ni qo'riqchi na "jimgina o'chirilgan", na "bitta metrika boshqasini shishirgan".

**Saboq — bu safar ikki tomonlama:** P13 da uchta nuqson topildi, va ulardan bittasi **kodda emas, hujjatda** edi. Ya'ni *"docstring — dalil emas"* qoidasi **ikki tomonlama** ishlaydi: **kodni hujjatga ishonib qabul qilish ham, hujjatni kodga ishonib qabul qilish ham** xato. Faqat **o'lchov** haqiqatni aytadi. Probe 63 o'lchangan xossa bo'yicha aynan shu ishni qiladi.


## Ultra-audit, 2026-09-20 — P12 va P13 ni qo'shni modullarga qarshi tekshirish

Foydalanuvchi topshirig'i bo'yicha **adversarial** tekshiruv: P12 va P13 qayta o'qildi, **avvaldan mavjud** modullar bilan kesishadigan joylari (P11 asset biri, `sheets` o'qish yo'li, `engine` darvozasi) ko'rib chiqildi, va chekkadagi mayda nuqsonlar izlandi. **Beshta haqiqiy nuqson** topildi va tuzatildi. Hammasi o'zim kiritgan va hammasi **jimgina** edi.

**1. `inf`/`nan` "raqam" sifatida o'tib ketardi — ikki modulda.** `_number` ning `isinstance(value, (int, float))` tarmog'i qiymatni **tekshirmasdan** `float()` qaytarardi:

```python
if isinstance(value, (int, float)):
    return float(value)          # nan ham, inf ham "o'qildi"
```

`float('inf') >= 0` **True**, shuning uchun cheksiz `run_seconds` yig'indiga **qo'shilib ketardi** va `availability = inf / planned = inf` chiqardi — quyi oqimdagi hech bir qo'riqchi buni tutmaydi, chunki `inf` — bu istisno emas, **qiymat**. Aynan o'sha teshik `manufacturing._number` da ham bor edi (P13 ga havola izohi bilan birga tuzatildi). Tuzatish:

```python
if isinstance(value, (int, float)):
    text = float(value)
    return text if -float('inf') < text < float('inf') else None
```

Ya'ni **cheksiz qiymat — bu son emas**, va katak *o'qilmaydigan* bo'ladi: sanaladi, lekin **hech qachon** ko'paytirilmaydi. Bu — P12 da o'rnatilgan `None ≠ 0` qoidasining davomi: **`None`** — yozilmagan, **`0`** — deklaratsiya qilingan nol, **`inf`/`nan`** — **o'qib bo'lmaydigan** katak. Uchtasi uch xil fakt.

**2. `andon` o'z ko'rsatish oynasidan narini **ko'rmasdi** — KRITIK.** `andon` `report(..., limit=limit)` chaqirardi va faqat **qaytgan** (ya'ni kesilgan) stansiyalarni chegaraga solishtirardi, keyin esa `complete: True` ni **qo'lda yozardi**. Natijada 60 stansiyali zavodda 51-stansiya chegarani buzsa, `andon` **`breach_count: 0`** qaytarardi — **yonayotgan zavod uchun**. Bu modul boshidanoq oldini olish uchun qurilgan sinf: *"truncation — bu **hisobotdagi** eslatma, lekin **signal egasidagi teshik**."* Tuzatish:

```python
result = report(engine, tenant, agent, step, limit=MAX_STATIONS)  # hammasini baholaydi
...
'stations': result['stations'][:limit],        # chaqiruvchining oynasi
'evaluated_count': len(result['stations']),    # baholanganlar soni
'complete': not result['truncated'],           # da'vo qilinmaydi — hosil qilinadi
```

Endi `limit` faqat **qaytariladigan ro'yxatni** chegaralaydi; chegara **baholash** dan ajratildi. `complete` esa **o'qishning o'zidan** hosil bo'ladi, chunki *"buzilish yo'q"* va *"ko'rgan qismimizda buzilish yo'q"* — ikki xil gap, va menejerga faqat bittasini ochiq aytish xavfsiz.

**3. `not_evaluated` **yalang'och son** edi.** Hisoblanmaydigan metrika `not_evaluated: 3` deb qaytardi — menejerga *"3"* **hech nima demaydi**: availability mi, quality mi? `not_computable` allaqachon **nomlarni** ushlab turadi, shuning uchun bu **o'sha tamoyilning buzilishi** edi. Tuzatildi: `not_evaluated_metrics` (nomlar) va `not_evaluated_stations` (`{'station': ..., 'metrics': [...]}`) qo'shildi. Son **qoldi** (chegara sozlovchi operator aynan uni grep qiladi), nomlar esa **yoniga** qo'shildi.

**4. BOM `truncated` chegarada **yolg'on gapirardi** — latent.** `'truncated': len(lines) >= MAX_BOM_LINES` **aynan** `200` qatorda `True` berardi, holbuki **hech nima tashlanmagan**. Sabab: loop `len(lines) >= 200` bo'lganda **keyingi** qatorni tashlaydi, ya'ni aynan 200 qatorli BOM **to'liq** va `complete` — lekin menejer *"ro'yxat kesilgan"* deb qolganini izlab ketardi. Tuzatildi: bayroq faqat **haqiqiy qator tashlanganda** ko'tariladi (`truncated = True; continue`).

**Muhim o'lchov: bu nuqson **latent** edi, va buni isbotlash uchun o'lchash kerak bo'ldi.** `sheets.MAX_ROWS = 200`, shuning uchun manba hech qachon 200 dan ortiq qator **berolmaydi**; `max_rows` ni 200 dan oshirib e'lon qilish ham **o'qishni yiqitardi** (`bounded_int(...1..200)`), holbuki `registers` uni **qabul qilardi** — **§142** buni **kesishga** o'zgartirdi. Demak BOM o'z chegarasidan **hech qachon** oshib keta olmaydi va `>=` xatosi hech qachon **ko'rinmaydi** — `truncated` faqat manbaning o'z bayrog'i bilan `True` bo'ladi. Shuning uchun test **integrativ emas, birlik** testidir: chegara **pasaytiriladi** (`patch('...MAX_BOM_LINES', 4)`), oshirishga urinish esa o'zi **xato** beradi. *"Latent" degani "yo'q" degani emas — u shunchaki hozircha jimgina.*

**5. Tekshirilgan va **sog'lom** topilgan joylar.** `assets.parse_path` bo'sh/`..`/noto'g'ri chuqurlikdagi yo'lni **rad etadi**; `sheets` `truncated: len(values) > limit` ni **noto'g'ri** hisoblardi — sarlavha qatorini **ma'lumot** sifatida sanardi; bu **§142 da o'lchanib tuzatildi**; `vision` `>` ni to'g'ri ishlatadi; `escalation` — P6 koordinatori, ikkinchi bildirishnoma yo'li **yo'q**; `speech` — Aisha REST adapterlari; `engine` darvozasi (`DIRECT_DESTINATION_FIELD`/`OUTBOUND_TOOLS`) joyida. Butun `platform_runtime` **kirill-lookalike** belgilar bo'yicha toza (`grep -P '[\x{0400}-\x{04FF}]'`).

**Regressiya va o'lchov.** Tuzatishlar uchun **17 test** qo'shildi (10 `test_oee.py`, 7 `test_manufacturing.py`) va **22 o'lchangan xossa** qo'shildi (15 `probe_oee_boundary.py` §7, 7 `probe_manufacturing_boundary.py` §7). Probe endi **78** (OEE) va **32** (ishlab chiqarish) xossani o'lchaydi. To'liq to'plam: **`Ran 1934 tests` → `failures=1, errors=149, skipped=1`** — baza **o'zgarmadi** (yagona yiqilish — Windows-only `test_all_files_private`).

**Saboq — bu safar uch tomonlama.** *"Docstring — dalil emas"* qoidasi **to'rtinchi marta** tasdiqlandi, va yana ikki yangi qatlam qo'shildi: (a) **"latent" ni ham o'lchash kerak** — kod chegaraga yetib borolmasa ham, bayroq **yolg'on gapirishi mumkin**, va uni topishning yagona yo'li chegarani **pasaytirib** ko'rish; (b) **"hammasini baholash" bilan "hammasini ko'rsatish" ni ajratish kerak** — chegara, son va da'vo (`complete`) bir-biridan mustaqil uchta narsa, va ularni bir o'zgaruvchiga bog'lash **signal egasida teshik** ochadi.

## Audit, 2026-09-20 — P14 A bosqichni o'z ichida tekshirish (probe bir nuqson topdi)

P14 ning birinchi bosqichi — **qo'ng'iroq hodisalari va rozilik darvozasi** —
qurilgandan keyin darhol probe bilan o'lchandi. Probe **bitta haqiqiy nuqson**
topdi, va u modulning eng markaziy da'vosini buzgan edi.

**Nuqson: "shaffof matn" deb o'qilgan ustun yozuv URL ini qayta chop etardi.**
Modul `outcome_column` ni "shaffof matn" sifatida o'qirdi va shu bilan
**audio hech qachon platformaga yetib kelmaydi** degan chegarani
**operator ustun nomini tanlashiga** bog'lab qo'ygan edi. Probe ko'rsatdi:

```json
{"outcome": "https://media.example/rec-9.wav"}
```

Ya'ni registrda media ustuni bo'lsa (yoki operator `outcome_column` ni xato
ustunga yo'naltirsa), **yozuv havolasi chiqishga tushardi** — va u yerdan
model kontekstiga va uchinchi tomonga. Bu aynan chegara taqiqlagan oqim.

**Tuzatish — strukturaviy, hujjat emas.** Lokator shaklidagi katak
**ushlanadi**: sxemali URL (`scheme://`, `//`, `data:`, `blob:`) yoki media
kengaytmali katak (`.wav/.mp3/.opus/.webm/.m4a/.aac/.pcm/.flac/.amr/.gsm/...`).
Bunday katak `outcome` maydonida **bo'sh** qaytadi va `withheld_outcomes` da
**sanaladi** — ya'ni operator ustuni media'ga qaratilganini **biladi**,
jimgina URL shaklidagi satr olmaydi. Filtr **tor**: `answered`, `no_answer` kabi
oddiy so'zlar o'zgarmaydi (bu alohida o'lchanadi).

**Test fixture'ning o'zida ham xato topildi.** Birinchi regressiya testi
**noto'g'ri sabab bilan** o'tayozdi: sarlavha (`header`) hali ham eski ustun
nomini (`natija`) tashiyotgan edi, config esa yangi ustunni
(`yozuv_havolasi`) ko'rsatgan edi — ya'ni modul **mavjud bo'lmagan** ustunni
o'qib, bo'sh natija qaytarardi va test "oqim yo'q" deb **yashil** bo'lardi,
holbuki oqim bor edi. Sarlavha config'ga moslashtirildi; probe ham xuddi shu
tuzoqqa tushgan edi va tuzatildi. **Saboq:** *chegara haqidagi test ham,
boshqa har qanday test kabi, noto'g'ri sabab bilan o'tishi mumkin — ayniqsa
"hech narsa chiqmasligi" ni tekshirgan test, chunki u **ham** "ustun mavjud
emas" tufayli o'tadi.*

**Xulosa.** *"Docstring — dalil emas"* qoidasi **beshinchi marta** tasdiqlandi,
va bu safar u eng qimmat joyda: **huquqiy** chegarada. Modulning markaziy
da'vosi — *"audio hech qachon yetib kelmaydi"* — dastlab **konfiguratsiyaga
bog'liq** edi, ya'ni hujjat to'g'ri, kod esa uni **majburlay olmasdi**. Faqat
probe buni ko'rsatdi; 54 test yashil edi, chunki ularning barchasi chegara
**to'g'ri sozlangan** holatda o'tardi.

---

## Audit, 2026-09-20 — P14 B bosqichni o'z ichida tekshirish (probe bitta nuqson topdi)

B bosqich qurilgach, u **o'z probe'i** bilan tekshirildi. Probe 4 ta
o'lchovda yiqildi; tekshiruv shuni ko'rsatdiki, **uchtasi probe'ning o'z
xatosi, bittasi modulning haqiqiy nuqsoni**.

### Probe'ning uchta xatosi — va nega ular muhim

1. **Bo'limlararo ifloslanish.** 8-bo'lim `h.payloads['calls']` ga satrlarni
   `append` qilardi, lekin oldingi bo'limlar o'sha payloadni **tashlab
   ketgan** edi. Natijada "5 ta chaqiriladigan qator" o'lchovi 6 chiqdi.
   **Tuzatish:** 8-bo'lim payloadni **o'zi** ochib e'lon qiladi (`=`, `+=`
   emas). *Saboq:* o'lchov asbobi ham o'lchanadigan holatni **o'zi
   qurishi** kerak — aks holda u o'tgan bo'limning qoldiqlarini o'lchaydi.
2. **Noto'g'ri kutilgan qiymat (saqlash).** Uzoq kelajakdagi `today` bilan
   ikkita **sanali** qator ham muddati o'tgan bo'ladi; probe bittasini
   kutgan edi.
3. Shu bilan birga **to'g'ri** xossa saqlab qolindi: `unaged` qator uzoq
   kelajakda ham `unaged` bo'lib qoladi — bu ayrim o'lchanadi.

### Modulning haqiqiy nuqsoni — yashirin O(N) kuchaytirish

Bu B bosqichning **eng qimmat topilmasi**, chunki u **to'g'ri natija berardi va
testdan o'tardi**.

`queue()` har bir chiqish qatori uchun `consent()` ni chaqirardi, `consent()`
esa har safar rozilik registrini **qaytadan o'qirdi**. Ya'ni:

- bitta qo'ng'iroq o'qishi + **N marta** rozilik o'qishi;
- 50 qatorli navbat = **50 marta** provayder GET.

Test buni **ushlamadi**, chunki u 2-3 qatorli fixture bilan ishlaydi va
**to'g'ri javob** oladi. Probe ushladi, chunki u **hop sonini** o'lchadi.

**Nega bu jiddiy:** navbat o'sgan sari xarajat chiziqli emas, **N marta**
o'sadi, va provayderning rate limiti aynan shu yerda buziladi — ya'ni
`throughput` (bizning sur'at chegaramiz) bilan **provayderning** chegarasi
bir-biriga zid ishlaydi.

**Tuzatish — ichki tikuv, yangi tool emas.** `consent()` ga `_index` parametri
qo'shildi: **bir marta o'qilgan** indeks. U tool yuzasida **yo'q** (tool
handler hech qachon uzatmaydi), shuning uchun bitta raqam haqidagi bitta savol
avvalgidek yangi o'qish oladi — ya'ni tuzatish **hech narsani
sekinlashtirmaydi**. `_scan()` (ya'ni `call_events`/`summary`) ham xuddi
shunday tuzatildi, chunki u ham qator boshiga `consent()` chaqirardi.

**O'lchov:** probe endi 12 qatorli ham, 60 qatorli navbat ham **2 hop**
sarflashini o'lchaydi (bitta qo'ng'iroq + bitta rozilik) — va bu **N ga
bog'liq emas**.

### Ikkinchi topilma — "sig'im nol" xavfsiz standart sifatida

Dizayn paytida aniqlanganki, sur'at e'lon qilinmagan tenant uchun eng xavfsiz
javob "cheksiz" emas, **nol**: sur'atini hal qilmagan operator hech kimga
qo'ng'iroq qilishni ham hal qilmagan. Bu `capacity = 0` va
`over_capacity == callable_count` bilan o'lchanadi.

**Xulosa.** *"Docstring — dalil emas"* qoidasi **oltinchi marta** tasdiqlandi,
va bu safar **unumdorlik** chegarasida: modul to'g'ri javob berardi, lekin uni
**N marta qimmatga** berardi. Buni faqat **hop sonini o'lchagan** probe
ko'rsatdi — natijani o'lchagan test emas. Bu P12/P13 darsining davomi: *test
"nima" ni o'lchaydi, probe "qancha" ni o'lchaydi, va chegara ko'pincha
"qancha" da yashaydi.*

## Audit, 2026-09-20 — P14 C bosqichni o'z ichida tekshirish

**Savol.** C bosqichning butun maqsadi — *"ikkinchi bildirishnoma yo'li
ochilmasin"* — haqiqatan bajarildimi yoki shunchaki shunday ko'rinadimi?

Bu **tuzilish savoli**, xatti-harakat savoli emas. Shuning uchun uni test bilan
emas, **o'lchov bilan** tekshirish kerak: modul nimani **eksport qiladi**, va
koordinator qaysi tool orqali **yuboradi**.

### Tasdiqlangan tuzilish

1. **Yuboradigan tool bitta.**
   `DELIVERY_TOOLS` C bosqichdan keyin ham
   **`frozenset({'telegram.send'})`** — bitta element ham qo'shilmagan. Telefon
   moduli hech qanday yuboruvchi tool **ro'yxatga olmaydi**; `telephony.*` beshta
   toolning hammasi **o'qish** (consent, call_events, summary, queue, retention).
2. **Manba — tanlov, yuboruvchi emas.**
   `SOURCES = ('workforce', 'telephony')`, `SOURCE_TOOLS = {'workforce':
   'workforce.workload', 'telephony': 'telephony.queue'}`. Ya'ni **yangi manba**
   qo'shildi, **yangi kanal emas**. `OUTBOUND_CHANNELS` o'zgarmadi:
   `{telegram, instagram}`.
3. **Ruxsat ikki tomonlama.** `configure()` manbaning **o'quvchisini** ham,
   `telegram.send` ni ham talab qiladi. Ya'ni telefon navbatini o'qish huquqi
   **yuborish huquqini bermaydi** — ikkalasi alohida.
4. **`source` sxemada saqlanadi** (`source TEXT NOT NULL DEFAULT 'workforce'`),
   eski yozuvlar sukut bo'yicha `workforce` — migratsiya **buzmaydi**.

### Skill-ga oid nozik joy — eskilik qoidasi

`_overdue_items` da **eskilik (staleness) faqat `workforce` ga** qo'llanadi.
Sabab: "3 kundan beri yangilanmagan ish" — bu **ish** haqidagi da'vo; lekin
"3 kundan beri yangilanmagan raqam" — bu **raqam** haqidagi da'vo, va u
ma'nosiz: raqam eskirgan degan tushuncha yo'q. Agar qoida manbasiz qo'llanilsa,
navbat qatorlari **jimgina yo'q bo'lardi** va sababsiz. Bu **manba-ko'lamli**
qoida, va probe buni alohida o'lchaydi.

### Uchta test xatosi (modul nuqsoni emas — o'lchov xatosi)

1. `test_a_telephony_escalation_names_no_person` — **assert juda qo'pol** edi:
   u butun dayjestda ism yo'qligini talab qildi, lekin dayjest **oyoq qismi**
   qonuniy ravishda *"xodimni baholamaydi"* deb yozadi — bu **kafolatning o'zi**.
   Tuzatildi: faqat **raqamlangan element qatorlari** skanerlanadi.
2. `test_the_digest_states_how_many_rows_the_gate_refused` — kapital harf
   (`Rad etilgan`) kutilgan, matnda esa `rad etgan`. Tuzatildi: `casefold`.
3. `test_preview_names_the_source_it_read` — preview **haqiqiy Sheets yo'lini**
   chaqirib `Explicit enabled Google OAuth configuration required` berdi.
   Tuzatildi: skriptlangan `_http_get` patch qo'yildi.

### Probe oltinchi bo'limi — va koordinatorning bir qadamlik tabiati

Probe 8-bo'limi "darvoza rad etgan raqamlar aytiladi" ni o'lchadi. U **yiqildi**,
va sabab **modulda emas, koordinatorda** edi: `tick()` **bitta** muddati kelgan
jadvalni oladi (`ORDER BY next_due, id LIMIT 1`). 7-bo'lim o'z jadvalini
**muddatli qoldirgan** edi, shuning uchun 8-bo'limda o'sha jadval **yana**
olingan. Tuzatish: keyingi bo'limlardan oldin oldingi jadval
`h.loop.disable(...)` bilan **o'chiriladi**.

**Nega bu muhim:** agar probe "o'lchayotgan narsasini" o'zi qurmasa, u
**boshqa bo'limning holatini** o'lchab qoladi. Bu B bosqichdagi "probe
arifmetikasi" xatosining **ikkinchi ko'rinishi** — bir marta payload'da, endi
**jadval holatida**. Saboq: *o'lchov asbobi o'z holatini o'zi e'lon qilishi
shart.*

### Xulosa

C bosqichda **modul nuqsoni topilmadi** — chunki C **kod qo'shmadi, yo'l
tanladi**. Uni tekshirishning to'g'ri usuli ham shu: **yangi yo'l yo'qligini**
o'lchash. Uchta test va ikkita probe xatosi — hammasi **o'lchov tomonida**,
modulning mantiqida emas.

*"Docstring — dalil emas"* ning yettinchi ko'rinishi shu: C ning docstring'i
"eskalatsiya orqali yuboriladi" derdi, va bu **to'g'ri** edi — lekin uni faqat
`DELIVERY_TOOLS` ning **o'zgarmaganini** ko'rsatish isbotladi.

## Audit, 2026-09-21 — ro'yxatga olish shartnomasi, dvigatel narxi va hujjat hajmi

Bu sessiya **optimallashtirish** sessiyasi: yangi blok qurilmadi, mavjud 2800 testli
to'plam, 12 670 satrli hujjat va 394 satrli skill **o'lchov bilan** qisqartirildi.
Uchta haqiqiy nomuvofiqlik topildi.

### 1. Ro'yxatga olish: bitta shartnoma, o'n uch nusxa, **ikki qarama-qarshi da'vo**

Runtime'da 17 ta `register_*` funksiya bor. **Ko'pchiligi** allaqachon mavjud tool'ni
o'tkazib yuboradi:

```python
if tool.name in registry.items:
    continue
```

Bu **shartnoma**, qulaylik emas: `build_registry()` ularni allaqachon chaqiradi, ya'ni
"registrni qurib, keyin bitta modulni qayta ro'yxatga olish" — **normal naqsh**. Qayta
chaqiruv `Invalid tool registration` bersa, xabar sabab haqida **hech narsa demaydi**.

**Besh modul** buni qilmagan: `business_graph`, `google_adapters`, `oversight`,
`sheets`, `workforce`. Ularda ikkinchi chaqiruv **ko'tarilardi**.

**Va eng yomoni:** `test_oversight.py` va `test_business_graph.py` **aynan shu
ko'tarilishni qadalgan** edi (`assertRaises(ValueError)`). Ya'ni chetlanish
**ataylab** ko'rinardi — keyingi o'quvchi uchun "bu tanlov" bo'lib o'qilardi.

**Tuzatish:** `tools.register_once(registry, tools)` — shartnomaning **yagona
bayoni**. Besh modul unga o'tkazildi, va 13 ta nusxa test **bitta** shartnoma
testiga birlashtirildi (`runtime_tests/test_registration_contract.py`), u butun
runtime bo'ylab **har bir** `register_*` ni aylanadi. Natija 13 → 4 test, lekin
**kuchliroq**: qo'lda yuritiladigan ro'yxat yo'q, shuning uchun keyin qo'shilgan modul
ham qamrab olinadi.

**Saboq:** bir tushunchani 13 marta takrorlash — bu shartnoma emas, **odat**; va uni
ikki marta inkor etish uni "tanlov" ga aylantiradi. Shartnoma **bir joyda** yozilsin.

### 2. Dvigatel narxi: har bir `Engine()` 12 sxema skriptini qayta bajarardi

`cProfile` (`test_supervisor.py`, 132 test, 38 s):

| Nima | Ulush |
|---|---|
| `Engine.__init__` | **14.5 s (38%)** |
| shundan `executescript` | **10.1 s** |
| `sqlite3.Connection.close` | 8.6 s |

Bitta faylda **144 ta `Engine()`** qurilishi → **1728** `executescript` chaqiruvi. Ya'ni
vaqt **modul mantig'ida emas**, har bir qurilishda **sxemani qaytadan yozishda**.

**Uchta o'lchangan tuzatish:**

1. **12 skript → 1 skript.** Har bir `executescript` — o'z tranzaksiyasi, ya'ni 12 ta
   commit. Birlashtirildi: **183 ms → 100 ms**.
2. **Barmoq-izi (`PRAGMA user_version`).** Sxemalar matnidan `sha256` olinadi, shuning
   uchun **qo'lda ko'tariladigan raqam yo'q**: biron modulga jadval qo'shilsa, matn
   o'zgaradi va keyingi `Engine()` sxemani **qayta qo'llaydi**. Raqam esdan chiqsa,
   natija — ishga tushishda emas, **runtime'da** yo'qolgan jadval.
3. **`synchronous=NORMAL` faqat sxema yozuvi uchun.** Xavfsiz, chunki yozuv
   **idempotent** va yo'qolgani barmoq-izi bilan **aniqlanadi** (jimgina yo'qolmaydi);
   WAL + NORMAL — SQLite hujjatlashtirgan sozlama. **100 ms → 45 ms.**

**Yakuniy: sovuq qurilish 183 ms → 44 ms (4×), issiq qurilish 13 ms.**

**Nima **qilmadi**: `scrypt` KDF (204 ms/chaqiruv) **ayblanmadi**, chunki o'lchandi —
butun to'plamda 91 chaqiruv, 18.6 s, **6%**. Undan muhimi: KDF'ni arzonlashtirish
**95 testni yiqitadi** (imzo `errors=149` → `244`), ya'ni u optimallashtirish emas,
**boshqa narsa**. Rad etildi va o'lchov skriptidan ham olib tashlandi.

### 3. Hujjat: 33 fayl → 16, va **mening xatom**

- 20 ta `V05*-IMPLEMENTATION-UZ.md` → **3 mavzuli fayl** (yadro / xabar almashish /
  operatsiyalar), har biri mundarija bilan. Yo'qotish **nol** — skript har bir manba
  satrini natijada qidiradi va topilmasa **to'xtaydi**.
- `§147.9` dagi takroriy darslar (7, 8, 9 = 1, 2, 4) olib tashlandi.
- `boundary-audit` skill: **394 → 73 satr** (har safar yuklanadigan qism), darslar 5 ta
  mavzuli `references/` fayliga ajratildi.

**Xato:** mundarija qo'shuvchi skript I fazani (§1–§10, 242 satr) **o'chirib yubordi**,
chunki qaytarish skripti "keyingi `# ` sarlavhagacha" yurdi va u 275 satr narida edi.
Repo **git emas** — tiklash yo'q edi. `.workbuddy-ai/memory/2026-09-20.md` dagi o'lchov
yozuvlaridan **qayta tiklandi**, hujjatga ochiq izoh qo'yildi (so'zma-so'z emas).

**Saboq — va u hujjatning o'z §147.6 darsining takrori:** oraliq o'chiruvchi skript
**ikkala uchni ham tasdiqlashi** kerak. Bu endi skill'ning `references/harness.md` da
yozilgan.

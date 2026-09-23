# v0.5 blok hisobotlari — xabar almashish (WhatsApp, hujjat)

WhatsApp kanali, kiruvchi oqim, oyna manbasi va hujjat qabul qilish. Bu bloklar bir oila: kanal, uning kiruvchi tomoni va oynani o'qish bir-birini tekshiradi.

## Mundarija

- [V05H — v0.5 blok: WhatsApp kanali (P8, T1)](#v05h)
- [V05I — v0.5 blok: Hujjat qabul qilish va hisob-kitob nazorati (P8b, T2)](#v05i)
- [V05J — v0.5 blok: WhatsApp kiruvchi oqim (P8c, T2) + adversarial audit](#v05j)
- [V05K — v0.5 blok: Oynani kiruvchi hodisalardan o‘qish (P8d)](#v05k)
- [V05L — V05L — `wa_window_until`: PRD §2.8 talabini tekshirish va rad etish](#v05l)

---

<a id="v05h"></a>

## v0.5 blok: WhatsApp kanali (P8, T1)

Implementatsiya hisoboti. Oldingi blok: `V05-BLOCKS-CORE-UZ.md` (P11b `vision_events`).

---

### 1. Nima qurildi

Yangi modul — `api-python/platform_runtime/whatsapp.py`. Uchta tool:

| Tool | Risk | Vazifa |
|---|---|---|
| `whatsapp.window` | **read** | Qaysi kontaktda 24 soatlik oyna ochiq va **qachon yopiladi** |
| `whatsapp.templates` | **read** | Operator e’lon qilgan shablonlar: nom + til + **toifa** |
| `whatsapp.send` | **write** | Bitta xabar: shablon, yoki **faqat oyna ichida** erkin matn |

Registry: `build_registry()` 59 → **62**, `known_tool_names()` 60 → **63**.

#### Nega bu blok approval naqshiga to‘liq tushadi

PRD §2.8 (manba: *WhatsApp Business API for AI Agents*, Bollard AI, 2026-06-23)
aynan bir gapni beradi: **oyna yopilganda erkin matn hech qachon yetib bormaydi** —
Meta xato **131047** qaytaradi. Ya’ni agent “mijozga yozdim” deb o‘ylaydi, mijoz
esa javob kutib qoladi. Bu **jimgina yo‘qotilgan xabar**, va u CRM’da “javob
berilgan” bo‘lib ko‘rinadi.

Shuning uchun blokning butun tuzilishi bitta qabul mezoniga bo‘ysunadi:

> **131047 ga yetib bo‘lmasligi kerak — dizayn bo‘yicha.**

Bu “tutamiz va retry qilamiz” degani emas. Bu **provider I/O dan oldin rad
etish** degani.

---

### 2. Rad etishlar — blokning asosiy qiymati

Blok funksiya emas, **chegara**. Shuning uchun eng muhim testlar “nima ishlaydi”
haqida emas, “nima rad etiladi” haqida.

| Rad etiladi | Sabab |
|---|---|
| Yopiq oynada **erkin matn** | 131047; **provider I/O dan oldin**, 0 POST |
| Yopiq oynada **e’lon qilinmagan shablon** | model shablon **yozmaydi**, faqat tanlaydi |
| Bir vaqtda **ham matn, ham shablon** (yoki ikkalasi ham yo‘q) | noaniqlik — xato, jim tanlov emas |
| `category` yoki `language` argumenti | toifani model tanlasa — sifat reytingi tushadi |
| **E’lon qilinmagan kontakt** | model bergan raqam — cheksiz send yuzasi |
| **E’lon qilinmagan registr** | bo‘sh registr “bo‘sh varaq” bo‘lib ko‘rinmasin |
| `api: on_premises` | On-Premises **sunset** (2025-10-23), faqat Cloud |
| Noma’lum registr kaliti (`camera_url` va h.k.) | typo ishlayotgan konfiguratsiyaga o‘xshamasin |
| `+` bilan yoki bo‘sh joyli telefon | E.164 talabi |
| Nomzodsiz (naive) vaqt tamg‘asi | **UTC+5** — 5 soatlik xato = oynadan tashqariga tushish |
| Noaniq (`10.01.2026`) vaqt tamg‘asi | taxmin qilish = noto‘g‘ri kunga tushish |

#### 2.1 Vaqt mintaqasi topilmasi

Sheets `dateTimeRenderOption=FORMATTED_STRING` bilan o‘qiladi, ya’ni sana katagi
odatda mintaqasiz keladi: `2026-09-19 10:00:00`. Dastlabki qoralama buni **UTC deb
qabul qilardi**. Bu jimgina noto‘g‘ri: O‘zbekiston **UTC+5**, ya’ni bunday o‘qish
oynani **5 soat erta** ochib, yuborishni oynadan tashqariga qo‘yardi — modul
aynan oldini olish uchun qurilgan xatoning o‘zi.

Tuzatildi: mintaqasiz vaqt tamg‘asi **rad etiladi**, `+05:00` yoki `Z` talab
qilinadi. `_parse_timestamp(ISO_Z) == _parse_timestamp(ISO_OFFSET)` testi qulflaydi.

---

### 3. O‘lchov: probe, e’lon emas

`scripts/probes/probe_whatsapp_window.py` haqiqiy `send` ni sanovchi transportga qarshi
yurgizadi. Natija (haqiqiy chiqish):

```
case                                   outcome                                                        POSTs
open window + text                     sent                                                           1
CLOSED window + text                   refused: Free-form text cannot be sent to 'dilnoza': the 24-hour serv 0
CLOSED window + declared template      sent                                                           1
open window + undeclared template      refused: Template 'welcome' is not declared for register 'support'. A 0

PROVEN: free-form text outside the window is refused with 0 provider
        POSTs and the refusal names error 131047, so the
        provider call that would produce 131047 is unreachable; a
        declared template is still delivered outside the window via
        the only path Meta permits. Window = 86400s.
```

Muhim raqam — **0**, “rad etildi” emas. Ma’lumotni o‘qib, keyin rad etadigan
darvoza — darvoza emas. Va **shablon yopiq oynada baribir ketadi**: blok WhatsApp
ni o‘chirmadi, Meta **ruxsat bergan yagona yo‘lni** ishlatdi.

---

### 4. Topilgan haqiqiy nuqsonlar

#### 4.1 `engine.py` chiquvchi manzil tekshiruvi qattiq kodlangan edi (haqiqiy, P8 dan oldin)

`engine._submit` chiquvchi manzilni tekshiradi — lekin toollar **qattiq
kodlangan ro‘yxat** bilan sanalgan:

```python
if step['tool'] in {'telegram.send','instagram.send'}:
```

Ya’ni har qanday **yangi** chiquvchi tool bu tekshiruvsiz qoladi. `whatsapp.send`
aynan shunday qoldi: modulning o‘z kontakt deklaratsiyasi himoya qilardi, lekin
`telegram.send`/`instagram.send` ega bo‘lgan **engine darajasidagi defence-in-depth**
qo‘llanmasdi — `allowed_recipients` allowlist’i umuman tekshirilmasdi.

Tuzatildi: **ikkita nomlangan konstanta** (`DIRECT_DESTINATION_FIELD`,
`OUTBOUND_TOOLS`) va kanalga bog‘liq qoida saqlandi:

```python
DIRECT_DESTINATION_FIELD = {'telegram.send': 'conversation_id',
                            'instagram.send': 'conversation_id',
                            'whatsapp.send': 'contact'}
OUTBOUND_TOOLS = frozenset(DIRECT_DESTINATION_FIELD)
OUTBOUND_CHANNELS = frozenset({'telegram', 'instagram'})
```

`OUTBOUND_CHANNELS` da WhatsApp **ataylab yo‘q**: inbound ingest hali yo‘q (u
webhook bloki), shuning uchun WhatsApp javobi hozircha **to‘g‘ridan-to‘g‘ri**
yuborish hisoblanadi va `allowed_recipients` talab qiladi. Bu fail-closed: ro‘yxat
e’lon qilinmasa — yuborish yo‘q.

2 yangi test buni qulflaydi: ro‘yxatda yo‘q kontakt `Forbidden`, ro‘yxatdagisi qabul.

**Nega bu muhim:** tekshiruv modulning o‘zida ham bor edi, lekin himoya
“modul eslab qolishiga” bog‘liq bo‘lsa, u himoya emas. Endi yangi kanal
qo‘shilsa, uni `DIRECT_DESTINATION_FIELD` ga qo‘shmaslik **ko‘rinadigan** qaror.

#### 4.2 Hujjatdagi registry soni noto‘g‘ri edi (oldingi sessiyadan)

P11b hisoboti “59 tools” deb yozgan. Haqiqiy son **62** edi
(`known_tool_names()` bilan **60**). Ya’ni oldingi blokning raqami bir birlikka
xato yozilgan va u keyingi hisobotlarga o‘tib ketgan. P8 dan keyin kanonik son —
**63** (`known_tool_names()`), katalogsiz `build_registry()` esa **62**.

Bu blokda tuzatildi va hisobotda ochiq qayd etildi, chunki **tekshirilmagan son —
tekshirilmagan da’vo**.

---

### 5. Testlar

`api-python/runtime_tests/test_whatsapp.py` — **52 test**, `unittest`, haqiqiy
`Engine` + haqiqiy SQLite, skriptlangan transport.

Guruhlar:

| Guruh | Soni | Nima qulflanadi |
|---|---|---|
| Oyna arifmetikasi | 10 | Sof funksiya; chegara aynan `last + 24h` da yopiladi |
| 131047 isboti | 9 | 0 POST; rad etish xatoni **nomlaydi**; shablon ichkarida ham, tashqarida ham |
| Shablon xavfsizligi | 7 | Toifa majburiy; `category`/`language` argument emas |
| Konfiguratsiya | 7 | Sunset API, noma’lum kalit, telefon shakli |
| Credential | 4 | Ikkita alohida kalit; send faqat o‘z kalitini o‘qiydi |
| Yuzalar | 8 | 3 nom; shablon yozish yo‘li yo‘q; broadcast yo‘li yo‘q |
| Vakolat | 3 | Sheets tool kerak; vakolat rad etilishi “bo‘sh ma’lumot” emas |
| Engine darvozasi | 2 | `allowed_recipients` tekshiruvi (yangi tuzatish) |

#### Test yozishda topilgan o‘z xatolarim

- `tools.config(tenant)` **tenant kaliti** bo‘yicha o‘qiydi; fixture’ni bosh
  darajaga qo‘ygan edim → 22 xato. `{TENANT: payload}` ga tuzatildi.
- `UTC` konstantasi noto‘g‘ri hisoblangan (`2026-09-19T05:00:00Z` = `1789794000`,
  men `1789812000` yozgandim) — test **arifmetikamni** tutdi, kodni emas.
- `patch(...)` konteksti **tugagach** `.calls` ni o‘qidim (`posted` o‘rniga
  `self.post`) — P11 dagi `_Sentinel` xatosining aynan o‘zi, takrorlandi.
- `contact` schema testida `type(...) == str` ni kutdim; schema — **dict**.
- “Ikkita credential ajratilgan” testini noto‘g‘ri yozdim: management yo‘qligi
  send’ni to‘xtatmasligi kerak edi. Ikkita aniq testga bo‘ldim (send o‘z kalitini
  o‘qiydi; messaging yo‘q bo‘lsa rad etiladi).

---

### 6. Konfiguratsiya va yordamchi skriptlar

- `config/whatsapp.example.json` — to‘qqiz `_note_*` kaliti bilan. Telegram farqi
  ham shu yerda hujjatlashtirilgan (xodim “Telegram’da ishladi, WhatsApp’da nega
  ishlamadi?” deb so‘raydi — javob: Telegram’da oyna yo‘q, WhatsApp’da bor).
- `scripts/check_whatsapp_example.py` — namunani modulning **o‘z** validatoridan
  o‘tkazadi; toifasiz shablon, sunset API, naive vaqt tamg‘asi va schema
  chegaralarini **o‘lchaydi**.
- `scripts/probes/probe_whatsapp_window.py` — §3 dagi isbot.
- `config/agent-capabilities.example.yaml` — `sales.whatsapp` (`human_assisted`,
  `allowed_recipients`) va `sales.support_view` (faqat o‘qish) qo‘shildi.
  Tekshiruv: 13 agent, 32 tool havolasi, 0 noma’lum.

---

### 7. Holat va ochiq ishlar

- To‘liq to‘plam: `Ran 1415 tests — failures=1, errors=149` — **bazaviy son
  o‘zgarmadi** (1363 + 52 yangi = 1415).
- Registry: `known_tool_names()` **63**.
- Live Meta qabul **NOT_RUN**: na token, na haqiqiy raqam, na Cloud API akkaunti yo‘q.
- **Ochiq:** inbound webhook ingest + event dedup + navbat (shu bilan
  `OUTBOUND_CHANNELS` ga `whatsapp` qo‘shiladi va javob o‘z inbound hodisasiga
  bog‘lanadi); o‘tkazuvchanlik (80 xabar/s) navbati; tier/limit kuzatuvi;
  UI; oyna `wa_window_until` atributi sifatida Business Graph’ning `customer`
  entity’siga (PRD §2.8 talab qiladi — **hali qilinmagan**).
- **Ochiq va hali tuzatilmagan (P11b dan):** `sheets.py` bo‘sh
  `allowed_connections` ni “hammasi mumkin” deb o‘qiydi, runtimening qolgani esa
  “hech narsa mumkin emas” deb (V05G §4.2).


---

<a id="v05i"></a>

## v0.5 blok: Hujjat qabul qilish va hisob-kitob nazorati (P8b, T2)

Implementatsiya hisoboti. Oldingi blok: `V05-BLOCKS-MESSAGING-UZ.md` (P8 `whatsapp_channel`).

---

### 1. Nima qurildi

Yangi modul — `api-python/platform_runtime/documents.py`. Beshta tool:

| Tool | Risk | Vazifa |
|---|---|---|
| `document.parse` | **write** | Ajratib olingan hujjatni normallashtiradi va qayd etadi |
| `document.match` | **read** | Uch tomonlama solishtirish: invoice / PO / delivery note |
| `document.duplicates` | **read** | Takroriy hujjatni topadi va **o‘zgargan summadan** ajratadi |
| `document.fraud_signals` | **read** | Firibgarlik **belgilarini** dalili bilan ko‘rsatadi |
| `document.posting_plan` | **write** | Rejani **yozadi**. Reja — taklif, to‘lov emas |

Registry: `build_registry()` 62 → **67**.

#### Blokning shakli PRD §2.5 dan olingan

PRD bitta zanjirni beradi:

> upload → parse → normalize → validate → duplicate check → PO/delivery matching
> (3-way) → exception and fraud controls → approval routing → payment and ERP sync
> planning → mock ERP post → audit log

Bu blok aynan shu zanjir, va u **allaqachon mavjud naqshlarning qayta
ishlatilishi**:

| PRD qadami | Qaysi mavjud naqsh |
|---|---|
| duplicate check | CRM/lead dedup — kalit `(tenant, connection, lead_id)` → hujjatda `(kind, supplier, number)` |
| 3-way matching | `reconcile` naqshi — uchta hujjat kelishishi kerak, natija **xabar qilinadi**, majburlanmaydi |
| fraud controls | **yangi** — bu blokning o‘z hissasi |
| posting plan | approval gate; **to‘lov yo‘q** |

---

### 2. “Platforma pul harakatlantirmaydi” — blokning butun ma’nosi

PRD §8 buni mahsulotning **o‘z ko‘lami** deb yozadi:

> Pul harakatlantirmaydi. Hujjatni tayyorlaydi, to‘lovni tasdiqqa qo‘yadi.

Ya’ni bu “to‘lashdan oldin so‘raymiz” emas. **To‘laydigan qadam umuman yo‘q.**
Bu kuchli da’vo, va kuchli da’vo docstring’da hech narsani isbotlamaydi — shuning
uchun u uchta mustaqil yo‘nalishdan **sinovdan o‘tkaziladi**:

1. **Sintaktik** (`test_the_module_has_no_function_that_moves_money`):
   modulning har bir ommaviy funksiyasi va klassi pul harakati lug‘ati bo‘yicha
   tekshiriladi. `pay_invoice` qo‘shgan kontribyutor shu yerda yiqiladi — bu
   ataylab: kafolat faylni o‘qimagan odamdan ham omon qolishi kerak.

2. **Registry orqali** (`scripts/probes/probe_document_no_payment.py`): beshta tool
   **uchta ladder**da (`human_led`, `human_assisted`, **`autonomous`**), haqiqiy
   `Engine` + haqiqiy SQLite, tarmoq o‘rnida **hisoblagich** bilan. Natija:

   ```
   tool                            human_led   human_assisted   autonomous  provider
   document.parse                     queued           queued       queued  0
   document.match                     queued           queued       queued  0
   document.duplicates                queued           queued       queued  0
   document.fraud_signals             queued           queued       queued  0
   document.posting_plan              queued           queued       queued  0
   ```

   15 katak, **hammasida 0 provider chaqiruvi**. `autonomous` — inson
   tasdiqlamaydigan sozlama; agar kafolat faqat `human_led` da ushlansa, u
   platforma haqida emas, bitta konfiguratsiya haqida bo‘lardi.

3. **Chiqishda**: reja shunchaki to‘lov qadamini **tushirib qoldirmaydi** — u
   ochiq aytadi:

   ```json
   {"executes_payment": false,
    "note": "This is a control plan, not a payment. The platform does not move
             money; posting to the ERP requires a human approval."}
   ```

   Rejani tasdiqlagan odam **nima bo‘lmaganini** ham bilishi kerak, faqat nima
   bo‘lganini emas.

---

### 3. Pul — bu butun son (minor units), hech qachon float

```python
if isinstance(value, float):
    raise ValueError('amount must not be a float: use an integer or a decimal '
                     'string, because binary floating point cannot represent '
                     'every decimal amount exactly')
```

Sabab oddiy: ikkilik suzuvchi nuqta har bir o‘nlik summani aniq saqlay olmaydi.
**Bir tiyin xato qiladigan hisob-kitob tekshiruvi — ishonib bo‘lmaydigan
tekshiruv.** Operator `"1250000.50"` (satr) yoki `1250000` (butun son) yozadi.

Yaxlitlash emas, **kesish** (`truncate`) ishlatiladi:

> Yuborilgan summani yuqoriga yaxlitlash keyinchalik solishtiriladigan sonni
> shishirardi.

Hujjat **o‘zi bilan kelishmasa** rad etiladi — satr elementlari yig‘indisi
ko‘rsatilgan jami summaga teng bo‘lishi shart:

```python
if items:
    summed = sum(item['amount'] for item in items)
    if summed != total_minor:
        raise ValueError(f'line items sum to {...} but the stated total is {...}: '
                         f'a document that disagrees with itself cannot be checked')
```

Aks holda PO bilan solishtirilgan jami — hujjatning o‘zi inkor qilayotgan son,
va noto‘g‘ri jami ustiga qurilgan keyingi tekshiruv **tekshiruvsizlikdan
yomonroq**.

Sana ham shunday: `10.01.2026` — yanvarmi yoki oktyabrmi? Taxmin qilish
dublikat oynasini **oylarga** suradi, shuning uchun noaniq format rad etiladi.

---

### 4. Dublikat kaliti — summani **ataylab** chiqarib tashlaydi

```python
def document_key(document):
    return (document['kind'], document['supplier'], document['number'])
```

Docstring:

> Deliberately excludes the amount. The same invoice number with a different
> amount is *more* suspicious than a same-amount repeat, not less.

Bu PRD’ning eng nozik talabi. Bir xil raqam, **boshqa summa** — bu qayta
chiqarilgan yoki o‘zgartirilgan hisob-faktura, ya’ni aynan shu tekshiruv
ushlaydigan firibgarlik. Qayta yuborilgan raqam **asl nusxani
qayta yozmaydi**: asl nusxa — birinchi marta ko‘rilgan narsaning **dalili**.

---

### 5. Uch tomonlama solishtirish — “yo‘q” va “zid” boshqa-boshqa faktlar

| Holat | Status | Ma’nosi |
|---|---|---|
| Ikkala hamkor ham yo‘q | `incomplete` | **Dalil yo‘q** |
| Bittasi yo‘q, qolgani mos | `partial` | To‘liq emas |
| Bittasi yo‘q, qolgani zid | `mismatch` | Zid dalil bor |
| Ikkalasi bor, mos | `matched` | — |
| Ikkalasi bor, zid | `mismatch` | — |

Bu ajratish muhim: **dalilning yo‘qligi va zid dalil — ikki xil fakt**, va
tasdiqlovchi ularni farqlay olishi kerak. `incomplete` ni `mismatch` deb
ko‘rsatish tasdiqlovchini yo‘q narsani qidirishga yuborardi.

`tolerance_minor` — operatorning o‘z ruxsati. **E’lon qilinmasa, talab qilingan
kelishuv aniq** (nol), chunki nolga teng bo‘lmagan default operator hech qachon
rozi bo‘lmagan nomuvofiqliklarni jimgina kechirardi.

Ikki xil valyuta kursisiz **hech qachon** solishtirilmaydi:

> Comparing across currencies without a rate would invent a number.

---

### 6. Firibgarlik: **belgi**, hukm emas — va material/kuchsiz ajratmasi

Blok ayblamaydi, baholamaydi, bloklamaydi. Har bir belgi **o‘z dalilini**
nomlaydi:

> a flag an approver cannot check is a flag an approver will learn to ignore.

Belgilar ikki guruhga bo‘linadi:

| Material (rejani **ushlab turadi**) | Kuchsiz (faqat **xabar qilinadi**) |
|---|---|
| `duplicate_altered` — o‘zgargan dublikat | `round_number` — yumaloq summa |
| `amount_outlier` — chetlangan summa | `weekend_date` — dam olish kuni sanasi |
| `bank_account_changed` — o‘zgargan bank hisobi | `no_history` — tarix yo‘q |

**Nega bu ajratish kerak bo‘ldi.** Dastlabki versiyamda barcha belgilar rejani
ushlab turardi. Natijada **toza, mos kelgan, yumaloq** hisob-faktura
`review_fraud_signals` olardi — chunki `round_number` ishga tushgan. Bu o‘zim
kodimdagi **dizayn ziddiyati**: haqiqiy hisob-fakturalarning ko‘pchiligi yumaloq,
shuning uchun bunga tayangan holda ushlab turish tasdiqlovchini
**ogohlantirishni o‘qimasdan bosishga o‘rgatadi**. Tuzatish: material belgilar
`needs_attention` ni majburlaydi, kuchsizlari esa baribir **ko‘rsatiladi** —
qaror insonda qoladi.

Outlier testi **ataylab qo‘pol** — o‘biroq “o‘sha yetkazib beruvchining o‘z
medianasidan uch baravar”, ya’ni *qarashga sabab*, statistik da’vo emas:

> saying so is more honest than dressing it up as a model.

`weekend_date` ham o‘z tarixini tekshiradi: agar yetkazib beruvchi **doim**
yakshanba kuni chiqaradigan bo‘lsa, dam olish kuni sanasi **oddiy**. Tarix
o‘qilmagan bo‘lsa, belgi ko‘tarilmaydi — va buni tekshirganimizni **da’vo
qilmaymiz**.

---

### 7. Topilgan **haqiqiy** nuqson: tool’lar engine darvozasidan o‘ta olmasdi

Bu blokda topilgan eng muhim xato — **mening o‘zim kiritgan** xato, va u
jimgina edi.

Birinchi versiyada sxema shunday yozilgan edi:

```python
fields = {'type': 'object'}
('document.parse', 'write', obj({'fields': fields}), _parse_tool),
```

Registry validatorida esa:

```python
props = schema.get('properties', {})
if set(value) - set(props) or set(schema.get('required', [])) - set(value):
    raise ValueError('Schema fields mismatch')
```

Ya’ni **`properties` e’lon qilinmagan** bare `{'type': 'object'}` uchun **har
bir** nested dict rad etiladi. Natijada beshta tool’ning **hammasi** har bir
chaqiruvni engine chegarasida rad etardi:

```
document.parse      ValueError: Schema fields mismatch
document.match      ValueError: Schema fields mismatch
document.duplicates ValueError: Schema fields mismatch
...
```

**Nega bu jimgina edi.** Registratsiya testlari o‘tdi: tool ro‘yxatda bor,
capability pack’da havola qilingan, `risk` to‘g‘ri. Lekin uni **submit qilib
bo‘lmaydi**. Naqd pul yo‘qolmaydi, xato chiqmaydi — shunchaki chaqiruv
ishlamaydi. Aynan shu turdagi nuqsonni testlar emas, **probe** topdi: probe
`submit` ni haqiqiy `Engine` orqali o‘tkazgani uchun `ValueError` hujayralari
darhol ko‘rindi.

Tuzatish — naqshni runtimening qolgan qismiga moslashtirish: **tuzilgan argument
JSON satri sifatida** yuboriladi, chunki hujjat — daraxt (satr elementlari,
hamkor hujjat), va uni bir qavatli skalar maydonlarga **shaklini yo‘qotmasdan**
yassilash mumkin emas.

```python
def _payload(args, key, *, required=False):
    """Decode one structured argument that travelled as JSON text. ..."""
```

Uchta regressiya testi qo‘shildi: tool haqiqiy chaqiruvni **qabul qilishi**
(`test_every_tool_accepts_a_well_formed_call`), buzilgan shakl validator
tomonidan **rad etilishi** (`test_a_nested_object_schema_would_reject_every_document`),
va haqiqiy engine orqali **oxirigacha** o‘tishi
(`test_the_chain_runs_end_to_end_through_the_real_engine`).

> **Sabab:** “tool ro‘yxatda bor” ≠ “tool ishlaydi”. Registratsiya testi
> ikkinchisini **hech qachon** tutmaydi.

---

### 8. Testlar

`api-python/runtime_tests/test_documents.py` — **84 test**, `unittest`, haqiqiy
`Engine` + haqiqiy SQLite.

Guruhlar:

| Guruh | Nima qulflanadi |
|---|---|
| Pul arifmetikasi (10) | Minor units; float **rad**; kesish, yaxlitlash emas; valyuta aniqligi |
| Hujjat yaxlitligi (10) | O‘zi bilan kelishmaslik **rad**; noaniq sana **rad**; noma’lum kalit **rad** |
| Dublikat kaliti (7) | Summa kalitda **yo‘q**; o‘zgargan dublikat material belgi |
| Uch tomonlama (14) | 4 status; `incomplete` ≠ `mismatch`; tolerantlik simmetrik; valyuta aralashmasi **rad** |
| Firibgarlik (14) | Har belgi dalili bilan; material/kuchsiz; tarixga tayangan dam olish kuni |
| Reja (10) | 5 tavsiya; `executes_payment` **False**; reja write paytida **qayta hisoblanadi** |
| To‘lov yo‘li yo‘q (3) | Tuzilma bo‘yicha: funksiya nomlari, tool nomlari, IBAN argumenti |
| Sxema yetib borishi (7) | **Yangi** — yuqoridagi nuqsonning regressiyasi |
| Payload dekoderi (6) | JSON satri; dict o‘tishi; buzuq JSON **toza rad** |
| Registratsiya (5) | 5 tool; idempotent; read/write ajratmasi |

#### Test yozishda topilgan o‘z xatolarim

- **`no such table: p_documents`** — engine faqat **o‘ziga aytilgan** sxemalarni
  yaratadi. `documents.SCHEMA` ni `engine.__init__` ga + `p_migrations` 8-versiya
  qo‘shildi.
- **`_amount_minor` satr shoxobchasi** chalkash va **noto‘g‘ri ko‘paytirardi**.
  `int(whole) * factor + (int(fraction) if digits else 0)` ga tuzatildi.
- **`round_number` aralash birlikda** hisoblanardi (minor units × o‘nlik raqam
  soni) — USD uchun **xato**. Minor units ichida hisoblanadi.
- **`weekend_date` yolg‘on da’vo qilardi** (“bu yetkazib beruvchi odatda
  chiqarmaydi” — tarixni tekshirmasdan). Faqat **o‘z tarixida** dam olish kuni
  **yo‘q** bo‘lganda ko‘tariladi.
- **Kuchsiz belgilar rejani ushlab turardi** → §6 dagi dizayn ziddiyati.
- **`documents_config` integratsiya fayli yo‘qligida `RuntimeError`** berardi →
  2 xato. Faqat fayl/blok **yo‘q** bo‘lganda `DEFAULTS` ga tushadi; **bor, lekin
  buzuq** blok qattiq xato bo‘lib qoladi (kalitdagi terish jimgina default bilan
  almashtirilmasligi kerak).
- **Outlier dalili turi** — `evidence['median']` **formatlangan satr** bo‘lib
  chiqadi (`'1100000'`), int emas. Test tuzatildi.
- **Tolerantlik simmetriyasi testi** `delivery_note=None` bergan edi → status
  `partial`, `matched` emas. Modul **to‘g‘ri** ishlagan; test tuzatildi.
- **Migratsiya testlari** — `test_v036_upgrade.py` va `test_v037_migration.py`
  `[1..7]` ni qattiq tekshiradi. 8-versiya qo‘shilgach ikkalasi ham yiqildi —
  bu **to‘g‘ri** testlar, shuning uchun ular `[1..8]` ga yangilandi (jimgina
  o‘chirilmadi).

---

### 9. Konfiguratsiya va yordamchi skriptlar

- `config/documents.example.json` — to‘qqiz `_note_*` kaliti bilan.
- `scripts/check_documents_example.py` — namunani modulning **o‘z** validatoridan
  o‘tkazadi; to‘lov shaklidagi kalit yo‘qligini, noma’lum kalit rad etilishini,
  manfiy tolerantlik rad etilishini, butun minor units + float rad etilishini,
  satr yig‘indisi mos kelmasligini **o‘lchaydi**.
- `scripts/probes/probe_document_no_payment.py` — §2 dagi isbot (4 bo‘lim).
- `config/agent-capabilities.example.yaml` — `finance.ap_clerk` (2 write + 3 read,
  `human_assisted`) va `finance.ap_reviewer` (faqat o‘qish) qo‘shildi.
  Tekshiruv: **15 agent, 37 tool havolasi, 0 noma’lum**.

---

### 10. Holat va ochiq ishlar

- Blok to‘plami: `Ran 84 tests — OK`.
- Registry: `build_registry()` **67**.
- Live inkassatsiya qabul **NOT_RUN**: na namuna hisob-faktura to‘plami, na
  haqiqiy ERP, na OCR yetkazib beruvchi.
- **Ataylab qilinmagan:** OCR/PDF kutubxonasi yo‘q. Maydonlar **allaqachon
  ajratilgan** holda keladi. Skanerlangan summani jimgina taxmin qilish uni rad
  etishdan **yomonroq**; ajratish sifati — ajratuvchining ishi, bu blok undan
  **keyingi** hamma narsani egallaydi va har qadami takrorlanadigan.
- **Ochiq:** ERP sinxronizatsiyasi (`mock ERP post` dan haqiqiy moslashuvga);
  `wa_window_until` atributi Business Graph’ning `customer` entity’siga (P8 dan
  meros, **hali qilinmagan**); `sheets.py` bo‘sh `allowed_connections` nomuvofiqligi
  (V05G §4.2, **hali tuzatilmagan**).
- **Rezidentlik:** hujjat va buxgalteriya ma’lumotlari uchta biometrik toifaga
  kirmaydi, shuning uchun 2026 tuzatishidan keyin O‘zbekiston tashqarisida
  saqlanishi **mumkin** — lekin uchta o‘tkazish asosidan biri qo‘llanilgandan
  keyingina, va amalga oshiruvchi reglamentlar **hali kuchga kirmagan**. Shuning
  uchun rezidentlik operator sozlamasi bo‘lib qoladi (`uz | any`).


---

<a id="v05j"></a>

## v0.5 blok: WhatsApp kiruvchi oqim (P8c, T2) + adversarial audit

Implementatsiya hisoboti. Oldingi blok: `V05-BLOCKS-MESSAGING-UZ.md` (P8b `document_intake`).

Bu hisobot ikki qismdan iborat:

1. **P8c blok** — yangi modul va uning kafolatlari.
2. **Adversarial audit** — yuqorida qilgan ishlarimni (P8, P8b, P8c) **mavjud
   logic va kodga qarshi** tanqidiy ko‘zdan o‘tkazish. Bu qism foydalanuvchi
   talab qilgan qism: *“avval yuqorida qilgan ishlaringni judda chuqur hammsini
   avvaldan bor bo‘lgan logic code lagoritimlar hammsini audit review tahlil qil
   adversary tanqidiy qarash logiclar bilan bu hato va muammoli emasmi yangi
   buglar chiqarmaaypmizmi qayta qayta tekshir to‘g‘ri ishlaypdimi har qanday
   vaziyatdaham”*.

---

## 1-qism. Nima qurildi (P8c)

Yangi modul — `api-python/platform_runtime/whatsapp_inbound.py`. Ikkita tool:

| Tool | Risk | Vazifa |
|---|---|---|
| `whatsapp.webhook` | **read** | Qabul qilingan, tashlangan va nima uchun — kiruvchi oqim hisoboti |
| `whatsapp.verify` | **read** | GET handshake javobi, tarmoqqa tegmasdan |

Registry: `build_registry()` 67 → **69**.

#### Bu blok **transport emas**

Modul baytlar va sarlavhalarni oladi va **qaror** qaytaradi. Ataylab: HTTP
framework — bu dependency, request obyekti — ishonchsiz kirishni tuzilgan
ko‘rinishga keltiradigan joy, va har bir frameworkning “men body’ni allaqachon
parse qildim” qulayligi — imzo tekshiruvini buzadigan narsaning aynan o‘zi. Route
yozgan dasturchi uchta qator ish qiladi (`raw bytes`, `X-Hub-Signature-256`,
`ingest`), va funksiyaning o‘zi **socket’siz testlanadi**.

#### Qoidalar, qo‘llanish tartibida

1. **Imzo RAW baytlar ustida, hech narsa parse qilinishidan oldin.**
   Meta **yuborgan aniq baytlarni** imzolaydi. Shuning uchun
   `verify_signature(body, …)` `bytes` oladi, dict emas — bu qulaylik emas,
   ataylab.
2. **Imzo yo‘qligi — rad, ogohlantirish emas.** Imzosiz endpoint URL’ni bilgan
   har kimga xabar yuborish imkonini beradi.
3. **Taqqoslash constant-time.** `hmac.compare_digest`. Erta chiqish soxta
   imzoning nechta bosh harfi to‘g‘ri kelganini oshkor qiladi.
4. **Faqat e’lon qilingan kontaktning xabari hodisa bo‘ladi.** Status callback
   (sent/delivered/read) va notanish raqam **sababi bilan tashlanadi**, raise
   qilinmaydi — Meta non-2xx ni qayta yuboradi, shuning uchun rad **qaror**
   bo‘lishi kerak, cheksiz retry bo‘roni emas.
5. **Dedup — engineniki, bu modulniki emas.** `accept_event` allaqachon
   `(tenant, channel, event_key)` bo‘yicha kalitlaydi va fingerprint
   ziddiyatini tekshiradi. Modul faqat **kalitni** beradi (WhatsApp message id).
   Ikki xil kalitli ikki dedup qatlami — dublikat tekshirilmagan qatlamdan
   o‘tishining yo‘li.

#### Blokning yagona qo‘shimchasi: **oyna atributi**

`window_until` — mijoz yozganini va qachon yozganini qayd etadi. Ilgari bu
faktni yozadigan hech narsa yo‘q edi: oyna operator qo‘lda yuritgan jadvaldan
o‘qilardi, ya’ni platformaning **qonuniy cheklovga javob berish huquqi** inson
timestamp yozishni eslab qolishiga bog‘liq edi.

---

## 2-qism. Adversarial audit — topilgan nuqsonlar

Bu audit **yangi bug chiqarmayapmizmi** degan savolga javob. Quyida topilgan
nuqsonlar **haqiqiy**, va ularning aksariyati **men o‘zim** kiritgan.

### 2.1. P8c: `window_until` yoziladi, lekin **hech kim o‘qimaydi** — eng katta topilma

`ingest` har bir qabul qilingan xabar uchun `window_until` ni saqlaydi. Lekin:

```
$ grep -rn "window_until" platform_runtime/ | grep -v whatsapp_inbound.py
(bo‘sh)
```

Ya’ni **kiruvchi oqim chiqish darvozasiga ulanmagan**. Aniqrog‘i:

```python
## engine.py
DIRECT_DESTINATION_FIELD = {'telegram.send': 'conversation_id',
                            'instagram.send': 'conversation_id',
                            'whatsapp.send': 'contact'}
OUTBOUND_CHANNELS = frozenset({'telegram', 'instagram'})   # whatsapp YO‘Q
```

`engine._submit` javobni tasdiqlangan kiruvchi hodisaga **faqat**
`OUTBOUND_CHANNELS` dagi kanallar uchun bog‘laydi (305-qator). WhatsApp bu
to‘plamda yo‘q, shuning uchun `whatsapp.send` **faqat statik
`allowed_recipients`** ro‘yxati bilan cheklanadi va oynani **operator
jadvalidan** o‘qiydi (`whatsapp.send` → `_read_last_inbound`).

**Nima uchun bu muhim:** oyna **ikki xil manbadan** o‘qiladi (kiruvchi hodisalar
va jadval), va ular bir-biriga mos kelishi kafolatlanmagan. Xabar qabul
qilinadi, oyna qayd etiladi, lekin javob berish qarori bu qaydga **qaramaydi**.

**Nima qildim.** (a) `engine.py` dagi eskirgan izohni tuzatdim — u
*“WhatsApp has no inbound ingest yet”* deb yozardi, bu endi **yolg‘on**;
(b) `_message` dagi **noto‘g‘ri da’voni** tuzatdim — u engine `_submit`
manzilni hodisaning `conversation_id` siga solishtiradi deb yozardi, WhatsApp
uchun bu **haqiqat emas**; (c) `BACKLOG.json` ga alohida ish birligi sifatida
qayd etdim.

**Nima uchun hozir birlashtirmadim.** Birlashtirish — bu **pretsedent qarori**
(kiruvchi hodisa ustunmi yoki jadvalmi?), va yarim bajarilgan birlashtirish
**ikki xil, bir-biriga zid oynani** majburlash demakdir. Bu alohida blok va
alohida probe talab qiladi.

### 2.2. P8: sana parseri **standart ISO formatni rad etardi** — haqiqiy bug

Bu eng jiddiy topilma. `_parse_timestamp` regexi:

```python
r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:?\d{2})'
```

Bu **kasr soniyani qabul qilmaydi**. Natija:

```
2026-09-20T10:24:50+05:00           -> 1789881890.0     ✅
2026-09-20T10:24:50.859388+05:00    -> None             ❌
2026-09-20T10:24:50.859Z            -> None             ❌
2026-09-20T10:24:50.123456789+05:00 -> None             ❌
```

`.859388+05:00` — bu `datetime.isoformat()`, JavaScript `Date.toISOString()`,
PostgreSQL va Go **standart bo‘yicha chiqaradigan** format. Ya’ni operator
jadvalida eng ko‘p uchraydigan shakl — parser rad etgan yagona shakl.

**Oqibati:** oyna `unparsable timestamp` deb **yopiq** o‘qiladi, va **hozir
yozgan mijozga** javob berish rad etiladi (“wait for the customer to message
first”). Bu — 131047 ni oldini olish uchun yozilgan modulning o‘zi keltirib
chiqaradigan nosozlik.

**Sabab:** regex o‘zi qo‘riqlayotgan `datetime.fromisoformat` dan **qattiqroq**
edi. Ya’ni parser buni har doim o‘qiy olardi — xato regexda edi.

**Tuzatish:** ixtiyoriy kasr qismi qo‘shildi (`(\.\d+)?`), va **ikki** regressiya
testi yozildi: kasr qabul qilinishi, va **kasr timezone talabini
yumshatmasligi** (fraction — bu aniqlik, offsetdan voz kechish uchun bahona
emas).

### 2.3. P8: testlar **durotka o‘rnatilgan** — to‘plam chirigan

`test_whatsapp.py` da:

```python
ISO_OFFSET = '2026-09-19T10:00:00+05:00'   # oyna ochilganini da’vo qilish uchun
```

24 soatlik oyna uni `2026-09-20T10:00+05:00` da yopadi. Audit **aynan shu kuni,
10:24 da** o‘tkazildi — ya’ni testlar 10:00 dan boshlab yiqila boshlagan va bu
**mening bugungi o‘zgarishlarimga aloqasi yo‘q edi**.

**Bu nuqsonning o‘ziga xos yomonligi:** test to‘plami **yozilgan kuni o‘tadi va
ertasi kuni yiqiladi** — ya’ni yashil to‘plam ishonchsiz bo‘lib qoladi.

**Tuzatish:** parsing fixture’lari (o‘zgarmas bo‘lishi **kerak**, ular satr
o‘qishni tekshiradi) va oyna fixture’lari (real soatga **nisbiy** bo‘lishi
kerak) **ajratildi**:

```python
UTC = 1_789_794_000.0            # parsing fixture — o‘zgarmas, ataylab
RECENT = time.time() - 60        # oyna fixture — nisbiy
STALE = time.time() - (WINDOW_SECONDS + 3600)
```

Injected `Clock` ham endi `RECENT` ga bog‘langan — engine soati “19-sentabr”
deb, oyna esa “bugun” deb turishi keyingi testni yiqitadigan nomuvofiqlik edi.

> Eslatma: `whatsapp_inbound` testlari bu kasallikdan **xoli**, chunki 2.1 da
> kiritgan `window_until` tuzatishim (Meta timestampidan hisoblash, host
> soatidan emas) ularni **tabiiy ravishda** vaqtga bog‘liq bo‘lmagan qilib
> qo‘ydi.

### 2.4. P8c: `MAX_BODY_BYTES` e’lon qilingan, **hech qachon qo‘llanmagan**

Konstanta docstring’da *“a body beyond this is not a message, it is an attempt to
make us allocate”* deb yozilgan, lekin **hech qayerda o‘qilmasdi**:

```
$ grep -rn "MAX_BODY_BYTES" platform_runtime/
whatsapp_inbound.py:75:MAX_BODY_BYTES = 1_000_000     # e’lon
(ishlatilish yo‘q)
```

Ya’ni o‘lchamsiz body hash qilinardi, parse qilinardi, yurilardi.

**Tuzatish:** chegara `verify_signature` ga qo‘yildi — **baytlar birinchi
ushlanadigan joy**, va HMAC dan **oldin**. Regressiya testi HMAC chaqiruvini
sanaydi va **0** bo‘lishini talab qiladi (ya’ni rad haqiqatan hash dan oldin).

### 2.5. P8c: `secret()` **raise qiladi**, shuning uchun o‘lik shoxobcha

```python
value = secret({...}, 'app_secret_env')
if not value:                       # ← yetib bo‘lmaydi
    raise WebhookError(...)
```

`tools.secret` **hech qachon falsy qaytarmaydi** — u `RuntimeError` ko‘taradi.
Ya’ni `if not value` **hech qachon** bajarilmaydi, va tashqariga xom
`RuntimeError` chiqib ketardi (webhook konfiguratsiyasi haqida emas, “missing
credential” haqida umumiy xabar).

**Tuzatish:** `secret()` `try/except RuntimeError` ichiga olindi va
`WebhookError` ga o‘girildi.

### 2.6. P8c: `whatsapp.webhook` sxemasi **ixtiyoriy argumentni majburiy** qilgan

```python
('whatsapp.webhook', obj({'limit': {...}}), _webhook_tool)   # required YO‘Q
```

`obj()` `required` berilmasa **hamma property’ni majburiy** qiladi, shuning uchun
argumentsiz chaqiruv `Schema fields mismatch` bilan yiqilardi — handler’ning
o‘zida `args.get('limit', 20)` borligi esa ixtiyoriylik **niyati** edi.

> Bu **P8b da topgan aynan o‘sha sinfdagi xato**, va men uni **takrorladim**.
> Bu auditning eng foydali topilmalaridan biri: bir blokda o‘rgangan saboq
> keyingi blokka avtomatik o‘tmaydi.

**Tuzatish:** `required=[]` (barcha sibling bare read toollar —
`graph.entities`, `supervisor.sections`, `asset.levels` — shunday qiladi).

### 2.7. P8c: `webhook_status` **mavjud bo‘lmagan ustunni** tanlagan

`SELECT … p_events … 'created'` — `p_events` da `created` ustuni **yo‘q**
(ustunlar: `tenant, channel, event_key, fingerprint, payload, status, claim,
lease, result, error`). Bu **mening testimda ham, shipping kodda ham** bor edi.

**Tuzatish:** `ORDER BY rowid DESC` (kiritish tartibi) va `lease` ustuni.

### 2.8. P8c: imzo prefiksi **registrga sezgir**, digest esa yo‘q

Prefiks `startswith('sha256=')` qattiq tekshirilardi, digest esa
`provided.lower()` bilan solishtirilardi. Ya’ni `SHA256=` yozgan spetsifikatsiyaga
mos yuboruvchi **prefiks testida** rad etilardi — autentifikatsiya xatosi kabi
ko‘rinadigan, aslida format nomuvofiqligi bo‘lgan rad.

**Tuzatish:** pastga tushirib `startswith` qilish.

### 2.9. P8c: `declared_contacts` **noaniq raqamni jimgina hal qilardi**

Bir raqam ikki registrda, ikki xil contact id bilan e’lon qilinsa, `{phone:
contact_id}` **oxirgi iteratsiya** yutardi — ya’ni **dict insertion tartibi**.
Konfiguratsiya faylini qayta tartiblash **jimgina** qaysi mijoz mijoz
ekanligini o‘zgartirardi.

**Tuzatish:** noaniqlik — **rad**, “oxirgisi yutadi” emas; xato xabari ikkala
registr va ikkala contact id ni nomlaydi. Bir xil raqam **bir xil** id bilan
ikki registrda — bu ziddiyat emas (regression testi ham yozildi).

### 2.10. P8c: `MAX_STATUS_CHARS` / `MAX_WA_ID_CHARS` — o‘lik konstantalar

Ikkalasi e’lon qilingan, hech qachon o‘qilmagan. **Tuzatildi:** o‘chirildi
(`MAX_TEXT_CHARS` haqiqatan ishlatiladi va qoldi).

### 2.11. P8b: probe **vakuum** edi (bu ham topilma)

`probe_whatsapp_signature.py` yozilayotganda “imzo raw baytlar ustida” degan
da’voni tekshirish uchun `json.dumps(json.loads(body))` ishlatdim — bu
**bayt-baytga bir xil** chiqdi:

```
reference : b'{"a": 1, "b": 2, "entry": [], "object": "x"}'
roundtrip : b'{"a": 1, "b": 2, "entry": [], "object": "x"}'
identical : True
```

Ya’ni probe **bir xil baytlarni** imzolab, ikkinchi satr deb atagan. Farq
**parse’dan o‘tishi kerak**: whitespace o‘tadi.

**Tuzatish:** `indent=2` bilan qayta seriyalash — `A != B` baytlarda, lekin
ikkalasi **bir xil obyektga parse bo‘ladi**. Endi haqiqiy tekshiruv.

---

## 3-qism. Audit xulosasi

| # | Nuqson | Jiddiylik | Holat |
|---|---|---|---|
| 2.1 | `window_until` o‘qilmaydi (integrasiya to‘liq emas) | **Yuqori** | Qayd etildi, ataylab ochiq |
| 2.2 | Kasr soniyali ISO timestamp **rad** etiladi | **Yuqori** | **Tuzatildi** + 2 test |
| 2.3 | Test fixture’lari durotka o‘rnatilgan (chirigan to‘plam) | **Yuqori** | **Tuzatildi** |
| 2.4 | `MAX_BODY_BYTES` qo‘llanmagan | O‘rta | **Tuzatildi** |
| 2.5 | `secret()` o‘lik shoxobcha | O‘rta | **Tuzatildi** |
| 2.6 | Ixtiyoriy argument majburiy (P8b xatosining takrori) | O‘rta | **Tuzatildi** |
| 2.7 | Mavjud bo‘lmagan `created` ustuni | O‘rta | **Tuzatildi** |
| 2.8 | Prefiks registrga sezgir | Kichik | **Tuzatildi** |
| 2.9 | Noaniq raqam jimgina hal qilinardi | Kichik | **Tuzatildi** |
| 2.10 | O‘lik konstantalar | Kichik | **Tuzatildi** |
| 2.11 | Probe vakuum edi | Kichik | **Tuzatildi** |

**Umumiy xulosa:** bloklar **to‘g‘ri ishlaydi**, lekin audit **uchta jiddiy
nuqson** topdi — va ularning **hammasi men o‘zim kiritgan**, hammasi
**jimgina** edi (naqd pul yo‘qolmaydi, xato chiqmaydi — shunchaki noto‘g‘ri
javob beriladi). Bu turdagi nuqsonni **testlar emas, audit va probe** topadi.

Eng muhim saboq: **bir blokda o‘rganilgan saboq keyingisiga o‘tmaydi** — P8b da
topilgan sxema xatosi P8c da **takrorlandi** (§2.6).

---

## 4-qism. Testlar

`api-python/runtime_tests/test_whatsapp_inbound.py` — **70 test** (1 skip),
`unittest`, haqiqiy `Engine` + haqiqiy SQLite.

`api-python/runtime_tests/test_whatsapp.py` — **54 test**, +2 yangi (kasr
soniyalar; kasr timezone talabini yumshatmasligi).

To‘liq to‘plam: **1571 test**, `failures=1, errors=149` — **o‘zgarmagan
baseline** (`LOCAL-VERIFICATION-UZ.md` ga qarang). Yangi blok baseline’ga
**hech qanday** yangi yiqilish qo‘shmadi.

---

## 5-qism. Konfiguratsiya va yordamchi skriptlar

- `config/whatsapp_webhook.example.json` — sakkiz `_note_*` kaliti bilan.
- `scripts/check_whatsapp_webhook_example.py` — namunani modulning **o‘z**
  validatoridan o‘tkazadi; kalitlar **qiymat emas, o‘zgaruvchi nomi** ekanini,
  app secret va verify token **farqli** ekanini, o‘rnatilmagan o‘zgaruvchi
  **rad** etilishini, buzuq havola **rad** etilishini, va token app secret ga
  **tushmasligini** o‘lchaydi.
- `scripts/probes/probe_whatsapp_signature.py` — 5 bo‘lim, `PROVEN` chiqaradi;
  nima **isbotlanmaganini** ham ochiq yozadi (2.1).
- `config/agent-capabilities.example.yaml` — o‘zgarishsiz (kiruvchi oqim tool
  emas, shuning uchun agent e’lon qilinmadi — ataylab).

---

## 6-qism. Ochilgan ishlar (backlog)

| Ish | Nima |
|---|---|
| `whatsapp_window_from_inbound_events` | `window_until` ni chiqish darvozasiga ulash (2.1): pretsedent qarori + probe |
| `wa_window_until attribute` | Grafikning `customer` entity’siga oyna atributi (PRD §2.8) |
| `sheets.py allowed_connections` | V05G §4.2 da qayd etilgan nomuvofiqlik | 
| ERP sync, `telephony_outbound`, `oee_and_andon`, `manufacturing_bom` | Yo‘l xaritasining qolgan bloklari |


---

<a id="v05k"></a>

## v0.5 blok: Oynani kiruvchi hodisalardan o‘qish (P8d)

Implementatsiya hisoboti. Oldingi blok: `V05-BLOCKS-MESSAGING-UZ.md` (P8c
`whatsapp_inbound_ingest` + adversarial audit).

---

### 1. Nima qurildi

P8c auditining **1-topilmasini** yopadi: kiruvchi blok oynani *yozardi*, lekin
**hech kim o‘qimasdi**. Endi o‘qiladi.

Yangi funksiya — `platform_runtime/whatsapp.py`:

| Funksiya | Vazifa |
|---|---|
| `_window_from_events(engine, tenant, contacts)` | Tasdiqlangan xabarlardan har bir kontakt uchun **oxirgi kiruvchi vaqt** |
| `window_sources(engine, tenant, agent, entry, step, contacts=...)` | Ikkala manbani birlashtiradi va **qaysi biri ustun** ekanini aytadi |

Ikkala chaqiruvchi ham yangilandi: `window()` (o‘qish) va `send()` (o‘qish
**darvozasi**). Registry **o‘zgarmadi** (69) — yangi tool yo‘q, chunki bu
modelga ochiq bo‘lmasligi kerak.

#### Pretsedent qoidasi — va nima uchun "yangrog‘i ustun" **emas**

```
tasdiqlangan hodisa (Meta imzolagan)   <- USTUN
operator jadvali (inson yozgan katak)  <- ZAXIRA
```

**Tasdiqlangan hodisa so‘zsiz ustun.** Sababi: platforma **o‘zi
tekshirmagan** qiymatdan o‘ziga ruxsat bera olmaydi. Agar qoida "yangrog‘i
ustun" bo‘lganida, operatorning qo‘lda yozgan katagi biroz kelajakdagi
timestamp bilan **yopiq oynani qayta ochib**, uni cheksiz cho‘zib qo‘yardi.
Bu — platformaning o‘ziga o‘zi ruxsat berishi.

**Jadval — zaxira, raqib emas.** Tasdiqlangan xabari yo‘q kontakt jadvaldan
o‘qiladi: ko‘chib o‘tayotgan tenant ishlashda davom etadi, va xabari kiruvchi
blokdan oldin kelgan mijozning javobi yo‘qolmaydi.

**O‘qib bo‘lmaydigan jadval — bo‘sh jadval emas.** Jadval o‘qilmasa
`status='unreadable'` qaytadi va hodisadan olingan qatorlar **baribir**
ishlatiladi: nosozlik javobni **to‘ldiradi**, o‘chirmaydi.

#### `source` maydoni — javob qaysi faktdan kelganini aytadi

Har bir qatorda endi `'source'` bor: `'event'` yoki `'register'`. "Nega
platforma bu mijozni oyna ichida deb o‘ylaydi" deb so‘ragan operator **Meta
imzolagan xabarmi yoki kimdir yozgan katakmi** — ko‘rishi kerak.

---

### 2. Yo‘l-yo‘lakay topilgan **haqiqiy** nuqson: oyna **ikki marta** qo‘shilardi

Bu blokdagi eng muhim topilma va u **mening o‘z kodimda** edi.

Ikki xil ifoda bor edi:

| Joy | Ma’nosi |
|---|---|
| `whatsapp_inbound` saqlaydi: `window_until` | **Tugash vaqti** = mijoz timestampi + 24 soat |
| `window_state(last_inbound)` kutadi | **Oxirgi kiruvchi vaqt**; 24 soatni **o‘zi** qo‘shadi |

Birinchi versiyam `window_until` ni to‘g‘ridan-to‘g‘ri `window_state` ga
uzatdi — ya’ni **24 soatni ikkinchi marta qo‘shdi**:

```
last_inbound          : 910000.0   (25 soat oldin -> YOPIQ)
window_until (tugash) : 996400.0   (1 soat oldin)
window_state(last_inbound) -> (False, 996400.0)   TO'G'RI
window_state(window_until)-> (True,  1082800.0)   XATO — ikki marta
```

**Oqibati:** 25 soat oldin yozgan mijoz **ochiq** deb hisoblanardi — bu modul
aynan oldini olish uchun yozilgan **131047** holati.

**Buni nima topdi:** test emas, **probe** (`probe_whatsapp_window_sources.py`,
2-bo‘lim). Chunki probe **yangi qiymatni eski hodisaga qarshi** qo‘yadi; test
esa faqat "ochiq" yoki "yopiq" ni tekshirardi.

**Tuzatish:** `_window_from_events` endi `window_until - WINDOW_SECONDS`
qaytaradi — ya’ni jadval yo‘li qaytaradigan **aynan o‘sha ifoda**. Konversiya
**bir marta**, nomlangan joyda.

Bu **P8c auditidagi bilan bir xil sinfdagi xato**: ikki modul bir tushunchani
ikki xil ifodalaydi, va ular orasidagi konversiya yo‘qoladi.

---

### 3. `OUTBOUND_CHANNELS` ga `whatsapp` **qo‘shilmadi** — ataylab

Auditda bu "ochiq gap" deb qayd etilgan edi. Batafsil o‘rganib, **qo‘shmaslik**
qaroriga keldim, chunki bu **boshqa savolga javob beradi**:

| Savol | Qayerda javob beriladi |
|---|---|
| Mijoz oyna ichidami? | `whatsapp.py` — **tasdiqlangan hodisadan** ✅ (bu blok) |
| Bu qadam bu manzilni nomlay oladimi? | `engine._submit` — paketning `allowed_recipients` ro‘yxatidan |

`whatsapp` ni to‘plamga qo‘shish `whatsapp.send` ni **allowlist shoxobchasidan**
**hodisaga bog‘lash shoxobchasiga** o‘tkazardi — ya’ni qoida **qo‘shilmaydi**,
**almashtiriladi**. Bu esa:
1. Oyna tashqarisida **qonuniy** sodir bo‘ladigan **shablon** yuborishni
   yiqitardi (unga bog‘lanadigan kiruvchi hodisa yo‘q).
2. Mavjud, sinalgan xatti-harakatni buzardi.

Shuning uchun ikkita tekshiruv **ajratilgan** holda qoldirildi, va bu
`engine.py` dagi izohda ochiq yozilgan.

---

### 4. Testlar

`api-python/runtime_tests/test_whatsapp.py` — **119 test** (54 → 119): yangi
`WindowSourceTests` sinfi 8 ta yangi test qo‘shadi va `WhatsAppTests` ni
meros oladi.

Yangi testlar:

| Test | Nima qulflanadi |
|---|---|
| `test_a_verified_event_wins_over_a_register_that_says_closed` | Hodisa jadvaldan ustun |
| `test_the_register_still_answers_for_a_contact_with_no_event` | Zaxira ishlaydi |
| `test_a_stale_register_cannot_reopen_a_window_the_events_closed` | "Yangrog‘i ustun" **emas** |
| `test_a_null_event_window_does_not_erase_a_register_window` | `None` oynani o‘chirmaydi |
| `test_the_send_gate_uses_the_verified_event` | Darvoza hodisani ishlatadi |
| `test_the_send_gate_still_refuses_when_the_event_window_is_closed` | Va aksi |
| `test_the_event_window_is_not_double_counted` | **§2 ning regressiyasi** |
| `test_a_message_just_inside_the_window_is_open` | Konversiya teskarisi |
| `test_the_newest_event_for_a_contact_is_the_one_used` | Eng yangi xabar |
| `test_another_tenants_events_do_not_open_this_tenants_window` | Tenant chegarasi |
| `test_a_broken_event_payload_is_skipped_not_fatal` | Buzuq qator o‘ldirmaydi |

#### Test yozishda topilgan **o‘z** xatolarim

- **`seed_inbound(window_until=None)`** "default ishlat" degani bo‘lib
  chiqdi, ya’ni `None` ni **ifodalab bo‘lmasdi** — test "null oyna" so‘rab,
  jimgina yaxshi oyna olgan. **Sentinel** (`_DEFAULT`) bilan tuzatildi.
- **`test_another_tenants_events...`** `open is False` ni kutgan edi, lekin
  fixture jadvali "ali bir daqiqa oldin yozdi" deydi. Modul **to‘g‘ri**
  ishlagan; test **manbani** tekshirishi kerak edi, holatni emas.
- **`places=3`** bilan `assertAlmostEqual` — bu **o‘nlik xona**, soniya emas.
  Kasr soniya farqi bilan yiqildi. **Tolerantlikka** o‘zgartirildi.
- **`base - 5*3600`** — `05:24:50Z` allaqachon `10:24:50+05:00` bilan **bir xil
  on**. Arifmetikam xato edi, modul emas.

---

### 5. Probe

`scripts/probes/probe_whatsapp_window_sources.py` — 4 bo‘lim, `PROVEN` chiqaradi:

1. **Faqat tasdiqlangan xabar oyna ochadi** — soxta imzo bilan 0 ta oyna.
2. **Eskirgan jadval yopiq oynani qayta ochmaydi** — *yangi* jadval qiymati
   *eski* hodisaga qarshi qo‘yiladi va **hodisa baribir ustun**.
3. **Zaxira ishlaydi** — tasdiqlangan xabari yo‘q kontakt jadvaldan o‘qiladi.
4. **Darvoza o‘sha fakt ustida ishlaydi** — oyna ochiq bo‘lsa yuboriladi, yo‘q
   bo‘lsa rad (provayder I/O sidan oldin).

Va nima **isbotlanmaganini** ham aytadi: jadvalning to‘g‘riligi — va bu
pretsedentning **butun ma’nosi**: endi u **to‘g‘ri bo‘lishi shart emas**.

---

### 6. Holat

- To‘liq to‘plam: `Ran 1638 tests` — **failures=1, errors=149**, baseline
  **o‘zgarmadi**.
- `test_whatsapp.py` → **119 test OK**.
- `probe_whatsapp_window_sources.py` → **PROVEN**.
- Registry: **69** (o‘zgarmadi — yangi tool yo‘q, ataylab).

### 7. Qolgan ish

- `wa_window_until` atributini Business Graph’ning `customer` entity’siga
  qo‘shish (PRD §2.8).
- ERP sinxronizatsiyasi (P8b davomi), `task_oversight_escalation` (P6),
  `manufacturing_bom` (P12), `oee_and_andon` (P13), `telephony_outbound` (P14).
- `sheets.py` `allowed_connections` nomuvofiqligi (V05G §4.2).


---

<a id="v05l"></a>

## V05L — `wa_window_until`: PRD §2.8 talabini tekshirish va rad etish

**Sana:** 2026-09-20
**Blok:** `wa_window_until_graph_attribute` (PRD v0.5 §2.8, 330-qator)
**Holat:** **TADQIQ ETILDI — QURILMADI.** Bu blok **ataylab rad etilgan**, va sabab
quyida o'lchov bilan ko'rsatilgan.
**Yangi tool:** yo'q. **Yangi test:** 6 ta metod (`WaLifecycleGuardTests`),
lekin ular `GraphTests` dan **meros oladi**, shuning uchun to'plamda **52** test
bo'lib ishlaydi — ya'ni qo'riqchi **ataylab** har bir mavjud graf sozlamasiga ham
qo'llanadi.

---

### 1. Talab, so'zma-so'z

PRD §2.8 (330-qator):

> Oyna holati **o'qilishi** kerak: `whatsapp.window` (read) — qaysi raqamda oyna
> ochiq va qachon yopiladi. Bu **Business Graph'ning `customer` entity'siga** yangi
> atribut bo'ladi: `wa_window_until`.

Ya'ni PRD "oyna **o'qiladigan** bo'lsin" deydi va buning yo'li sifatida Business
Graph'ga yangi **atribut** taklif qiladi.

### 2. Nima uchun atribut **mumkin emas** — mexanik sabab

Business Graph'da **atribut — bu ustun nomi**, xolos. `_source(entry, name, source)`
`map` ni shunday o'qiydi:

```python
clean_map[attribute] = _text(field, f'{entity}.{name}.{attribute}', 64)
```

va o'qishda `_collect`:

```python
value = row.get(field)      # <-- to'g'ridan-to'g'ri provider qatoridan
```

Ya'ni `wa_window_until` ni `customer` entity'siga atribut qilib qo'shish, amalda,
**Sheet'da yoki CRM'da `wa_window_until` degan ustun bo'lishi kerak** degani. Va
o'sha ustun **mijoz yozganda o'zi yangilanmaydi** — uni kimdir qo'lda yangilashi
kerak.

Bu P8d da yopilgan nuqsonning **aynan o'zi**, faqat yangi qatlamda:

| | P8c | P8d | P8L (taklif) |
|---|---|---|---|
| Oyna manbasi | qo'lda yozilgan Sheet ustuni | Meta imzolagan hodisa **ustun** | yana qo'lda yozilgan ustun |
| Kim yangilaydi | operator | Meta (o'zi) | operator |
| Natija | 131047 | tuzatildi | 131047 **qaytadi** |

P8d da **aynan** shuning uchun `window_sources` yozildi: qo'lda tahrirlangan
katak Meta o'zi bergan timestampni **bosib ketmasligi** kerak. `customer`
entity'siga `wa_window_until` atributi qo'shilsa — hatto niyati yaxshi bo'lsa ham —
platforma o'zining **tasdiqlangan** faktini qo'lda kiritilgan katak bilan
**almashtiradi**.

### 3. Nima uchun `whatsapp.window` ni graf manbasi qilib bo'lmaydi

Ikkinchi yo'l bor edi: `wa_window_until` ni hisoblangan atribut qilib, uni
`whatsapp.window` tool'i orqali to'ldirish. **Bu ham rad etildi**, va sabab kuchli:

1. **`SAFE_SOURCE_TOOLS`** faqat `{connectors.read, sheets.rows, sheets.read,
   database.read}`. `whatsapp.window` ni qo'shish grafning "manba — bu mavjud
   **read** tool" kontraktini buzadi, chunki keyingi blokda har qanday boshqa
   hisoblangan read ham xuddi shu yo'lni talab qiladi va ro'yxat o'sib ketadi.
2. **Graf manbalari argumentli.** `whatsapp.window` esa `register` ni **o'z
   konfiguratsiyasidan** oladi (`_registers(tenant)`), `entity_id` ni emas. Ya'ni
   graf `customer` id sini (`'ali'`) o'sha tool'ning kontakt id siga **bog'lay
   olmaydi**; graf `customer.id = 'ali'` deb o'ylaydi, `whatsapp.window` esa
   `register-1.contacts['ali']` ni ko'radi. Ularni bog'lash operatorga **ikkinchi
   marta** deklaratsiya qildirishni talab qiladi.
3. **Graf o'qish shakli mos emas.** Graf manbasi `rows: [ {...} ]` qaytaradi va
   `key` maydoni bo'yicha bucket'lanadi. `whatsapp.window` esa
   `{registers:[{contacts:[...]}]}` qaytaradi — adapter yozish kerak bo'lardi,
   ya'ni grafning "manba — mavjud tool, yangi transport yo'q" degan asosiy
   kafolati buzilardi.
4. **Vakolat ikki marta tekshiriladi, lekin kalitlar boshqa.** Graf `preflight` da
   `whatsapp.window` ni talab qiladi; `whatsapp.window` esa o'z navbatida
   `sheets.rows` ni talab qiladi. Natijada **uch** ruxsat bitta savol uchun kerak
   bo'lardi. Bu xato emas, lekin murakkablik **foyda bermaydi**: `whatsapp.window`
   allaqachon aynan shu savolga javob beradi.

### 4. Nima uchun allaqachon **bajarilgan**

PRD ning **maqsadi** — "qaysi raqamda oyna ochiq va qachon yopiladi" — P8 va P8d
bilan **to'liq** bajarilgan:

| PRD talabi | Bajarilgan joyi | Holat |
|---|---|---|
| Oyna **o'qiladigan** bo'lsin | `whatsapp.window` (read) | ✅ P8 |
| Qaysi raqamda ochiq | `contacts[].open` | ✅ P8 |
| **Qachon** yopiladi | `contacts[].closes_at` | ✅ P8 |
| Oyna **haqiqiy** manbadan | `window_sources` → `source: 'event'` | ✅ **P8d** |
| Telegram'da oyna yo'qligi hujjatlashtirilsin | `README` + `whatsapp.py` docstring | ✅ P8 |

Ya'ni PRD §2.8 ning **3-bandi** ("oyna o'qilishi kerak") bajarilgan; faqat PRD
o'ylagan **implementatsiya yo'li** (graf atributi) noto'g'ri. Talab emas, **usul**
rad etilmoqda.

### 5. Buning o'rniga: javob **bir joyda** qoladi

O'rniga **regressiya qo'riqchisi** yozildi — `business_graph` `customer`
entity'sida `wa_window_until` degan atribut **paydo bo'lmasligi** kerak, chunki
agar kelajakda kimdir uni qo'shsa, u jimgina **eski, ikkinchi manba** bo'lardi.

`api-python/runtime_tests/test_business_graph.py` ga **15 ta mavzuli da’vo**
yozildi, 6 ta yangi metod sifatida, `WaLifecycleGuardTests` da — ular
`GraphTests` dan meros oladi, shuning uchun to‘plamda **52** test sifatida
ishlaydi:

- `test_wa_window_until_is_not_a_declared_graph_attribute` — umumiy qo'riqchi:
  **hech qanday** entity, **hech qanday** manbada `wa_window_until` yo'q;
- `test_a_window_attribute_would_come_from_the_provider_row` — atribut =
  `row.get(field)` ekanini o'lchaydi, ya'ni atribut **hisoblanmagan**, **ko'chirilgan**
  qiymat ekanini isbotlaydi;
- `test_a_sheet_window_column_is_only_as_fresh_as_the_person_who_typed_it` —
  P8c nuqsonining takrorlanishini **kitobiy** ko'rsatadi: Sheet'dagi katak
  yangilanmasa, o'qilgan qiymat eskiradi;
- `test_whatsapp_window_reports_source_so_the_graph_has_nothing_to_add` — graf
  qo'shadigan ma'lumot yo'qligini ko'rsatadi: `whatsapp.window` allaqachon
  `source` maydonini beradi.

### 6. `window_until` **atribut sifatida** qayerga tegishli — maslahat

Agar kelajakda **haqiqatan** grafda oyna kerak bo'lsa, to'g'ri yo'l — `map` emas,
balki `sheet` **manbasining o'zi** orqali emas, balki **`whatsapp.window` ni
`SAFE_SOURCE_TOOLS` ga qo'shish va `rows` adapterini yozish**. Hozir bu **kerak
emas**, chunki:

- `whatsapp.window` bitta tool chaqiruvida **barcha** kontaktlar uchun javob
  beradi — graf esa **bitta** entity beradi, ya'ni graf **ko'proq** I/O qilardi;
- oyna **real vaqt** hodisasi (`now`), graf esa **surat** (`observed`). Ikkalasini
  aralashtirish operatorga "graf 10 daqiqa oldin oynani ochiq deb o'ylagan" degan
  xulosani beradi — bu P8d da ataylab qochilgan xato sinfi.

Shuning uchun **`wa_window_until` atributi qo'shilmaydi**, va PRD §2.8 ning shu
bandi **"boshqa yo'l bilan bajarildi"** deb hisoblanadi.

### 7. Tekshiruv natijasi

| Tekshiruv | Natija |
|---|---|
| `... -p "test_business_graph.py"` | **98 test OK** (46 + 52 yangi sirt) |
| To'liq to'plam | `Ran 1688 tests — failures=1, errors=149, skipped=1` |
| Baseline | **o'zgarmadi** (1636 → 1688; +52 yangi test, error soniga qo'shilmadi) |
| Registry | `build_registry()=69`, `known_tool_names()=70` — **yangi tool yo'q** |

Yagona `failures=1` — Windows-only `test_all_files_private` (`0 != 63`), u P8 dan
oldin ham bor edi.

### 8. Xulosa

**PRD talab qilgan narsa bajarildi; PRD taklif qilgan usul rad etildi.**

- Oyna **o'qiladigan** (P8) va **haqiqiy** manbadan o'qiladi (P8d).
- Grafga atribut qo'shish oynani **qo'lda yozilgan ustunga** qaytarardi, ya'ni
  P8d da yopilgan 131047 nuqsonini **qayta ochardi**.
- Buning o'rniga qo'riqchi testlar yozildi, shunda kelajakda bu xato
  **jimgina** qaytib kelolmaydi.
- **Yangi tool yo'q** (`build_registry()=69`): blok yangi imkoniyat ochmaydi —
  u faqat **noto'g'ri** imkoniyatni yopadi.

# v0.4 implementation: Business Graph PRD va Sheets registers

## Scope

Ikki qism:

1. **PRD** — mijozning real feedbackidan chiqqan yangi mahsulot yo'nalishi:
   `docs/prd-v04/00-UNIFIED-BUSINESS-BRAIN-PRD-UZ.md`
2. **Birinchi source blok** — P3 (`google_sheets_multi`) ning read qismi:
   `platform_runtime/sheets.py`

Production qarori o'zgarmaydi: **NO_GO**. Live Google acceptance yo'q.

## 1. PRD: mijoz feedbackining asosiy xulosasi

Mijozning tizimi bugun 6 ta tarqoq bo'lak: AmoCRM/Bitrix24 (sotuv + IP telefoniya),
MoySklad (tovar), 1C (buxgalteriya), Google Sheets (moliya), yana Sheets (HR),
alohida tizim (soliq), va ishlab chiqarish.

Mijozning eng qimmat gapi: **"Odonni o'rgatish muammo"**. Bu butun pozitsiyani belgilaydi:

> Platforma mavjud tizimlarni **almashtirmaydi**. U ularning **ustida** turadi.

Xodim uchun hech narsa o'zgarmaydi — u o'z ekranlarida ishlashda davom etadi. Farq:
endi **o'zbekcha so'raydi** va javobni hamma tizimdan bir vaqtda oladi.

PRD'da halol gap bor: biz "hamma narsani umumlashtiradigan tizim" ni **read-side**da
qilamiz. Write-side xavfli (narx ziddiyati, double-entry) — shuning uchun faqat
manba egalari yozadi, qolganlari read yoki derived.

## 2. Ish qismi: `platform_runtime/sheets.py`

Moliya va HR bugun **allaqachon Google Sheets'da**. Demak ular uchun **hech narsa
o'rgatish kerak emas** — bu eng arzon birinchi qadam.

### Nima qo'shildi

Operator `sheets_registers`da **nomlangan registrlarni** e'lon qiladi:

```json
"sheets_registers": {
  "finance": {
    "connection": "google",
    "spreadsheet_id": "1AbC...",
    "header_row": true,
    "max_rows": 200,
    "ranges": {"revenue": "Kunlik!A1:F", "expenses": "Xarajat!A1:D"}
  }
}
```

Uchta yangi tool:

| Tool | Xavf | Vazifa |
|---|---|---|
| `sheets.registers` | read | E'lon qilingan registrlar va range nomlari (spreadsheet_id **qaytarilmaydi**) |
| `sheets.read` | read | Bitta range'ni xom matritsa sifatida o'qish |
| `sheets.rows` | read | Bitta range'ni **sarlavha bo'yicha kalitlangan obyektlar** sifatida o'qish |

### Xavfsizlik chegaralari

1. **Manzil faqat operator konfiguratsiyasi.** `spreadsheet_id`, `range` va token
   hech qachon plan, pack yoki model javobidan olinmaydi. Schema
   `additionalProperties: false` — qo'shimcha argument rad etiladi.
2. **E'lon qilinmagan registr = rad etish**, bo'sh varaq emas. Bo'sh o'qish haqiqiy
   ma'lumotdek ko'rinadi, shuning uchun eng xavfli xato shu bo'lardi.
3. **A1 notation qat'iy tekshiriladi:** `S!A1;B2`, `=SUM(...)`, `../etc`, `S'!A1` rad
   etiladi. Kirillcha varaq nomlari (`Лист!A1:F`) qabul qilinadi — bu o'zbek/rus
   jadvallarida normal.
4. **Konfiguratsiya xatosi jimgina o'tkazilmaydi.** Bitta registrdagi xato `ValueError`
   beradi, chunki jimgina o'tkazib yuborilgan registr "ma'lumot yo'q" bo'lib ko'rinardi.
5. **Javob chegaralangan:** qatorlar (`max_rows`, `MAX_ROWS=200`), katak uzunligi
   (`MAX_CELL_CHARS=200`), umumiy bayt (`200KB`). Bitta keng jadval planner kontekstini
   jimgina portlatmasligi kerak.
6. **`!` va `:` percent-encoded** — e'lon qilingan range o'z path segmentidan chiqib
   keta olmaydi.
7. **Provider xatosi sanitized** — faqat HTTP status, javob tanasi emas (u range yoki
   internal URL'ni qaytarishi mumkin).

### Muhim qaror: write qo'shilmadi

Mavjud `sheets.append` (bitta e'lon qilingan range, approval-gated) **o'zgarmadi**.
Ko'p-range write uchun Google write yo'lidagi **transactional dispatch fencing** kerak;
undan zaif ikkinchi write yo'li qo'shish **yo'qligidan yomonroq** bo'lardi.

### Sarlavha (header) qoidalari

- Bo'sh sarlavha katagi → `column_1` (pozitsion nom).
- Ikki bir xil sarlavha → `Summa`, `Summa_2`. Aks holda lookup **noto'g'ri ustunni**
  jimgina o'qirdi.
- Qisqa qator → bo'sh katak bilan to'ldiriladi, **tashlanmaydi**.
- Uzun qator → qo'shimcha ustunlar `column_N` bo'ladi.
- Raqam va boolean **matnga aylantirilmaydi** — moliya uchun arifmetika ishlashi kerak.

## 3. Darhol foyda — aniq mijoz savoli

Mijoz: *"Uning narxlarini olib yurish ham muammo"*.

Endi moliyachi Telegram'da so'raydi: *"Kechagi tushum"* → agent `sheets.rows` bilan
`finance.revenue` ni o'qiydi → javob **manbasi bilan** qaytadi. Excel ochish, varaq
almashtirish, sana filtri — hech biri kerak emas.

## 4. Dalil

| Guruh | Test |
|---|---:|
| `runtime_tests/test_sheets.py` | 25 |
| Jami yangi | **25** |

`python -m unittest discover -s runtime_tests`:

| | Oldin | Keyin |
|---|---:|---:|
| Topilgan test | 1026 | **1051** |
| Error | 150 | **150** |
| Failure | 1 | **1** |

To'liq offline harness: `release_tools` PASS (3), `manifest_tools` PASS (6),
`sqlite_demo` PASS, `managed_database_demo` PASS, uchta browser suite PASS (40),
`python_syntax` PASS. Yagona failure — POSIX-only `test_all_files_private` (o'zgarmagan).

Ilgari mavjud guruhlar ham o'tdi: `test_sheets` + 6 ta CRM/reengagement/pack guruhi =
**147/147**.

## 5. Test topgan haqiqiy nuqsonlar

| Topilma | Nega muhim |
|---|---|
| A1 regex `A1:F` (butun ustun) ni rad etgan | Mijozning haqiqiy jadvallari `A:F` va `1:5` shaklini ishlatadi; validator amalda ishlamas edi |
| `register_crm_tools(r)` **ikki marta** chaqirilgan | Registry'ning o'z dublikat qo'riqchisi ushladi — bu butun tool ro'yxatini buzardi |
| Bo'sh va takroriy sarlavha | Takroriy sarlavha noto'g'ri ustunni jimgina o'qirdi |

## 6. Chegaralar (yashirilmaydi)

1. **Live Google acceptance yo'q.** OAuth token, haqiqiy spreadsheet, quota va
   rate-limit tekshirilmagan. Test `configured_manager`ni almashtiradi.
2. **Ko'p-range write yo'q.** Faqat mavjud `sheets.append` (bitta range).
3. **Business Graph yo'q.** Hozir har bir registr alohida o'qiladi; kross-tizim
   birlashtirish (P1) keyingi blok.
4. **`sheets.registers` spreadsheet_id qaytarmaydi** — bu ataylab, lekin operator UI
   uchun alohida ko'rinish kerak bo'lishi mumkin.
5. **Nomlangan range qo'llanmaydi** — faqat A1. Bu ataylab: tekshirilmagan nomlangan
   range kutilmagan varaqni ochishi mumkin.
6. **Formula natijasi** `UNFORMATTED_VALUE` bilan olinadi, ya'ni qiymat, formula emas.
   Moliya uchun to'g'ri, lekin "qaysi formula" savoliga javob bermaydi.

## 7. Keyingi blok

**`business_graph`** (P1) — kross-tizim read model. `sheets.py` uning birinchi manbasi
bo'ladi. Har bir atribut manbasi va ziddiyati ko'rsatiladi; `conflict_policy: report`
default.

Keyin **`inventory_moysklad`** (P4), chunki mijoz narx/qoldiq og'rig'ini ikki marta aytdi.
# CodeBurn kontekst auditi — Agent Platform

**Asbob:** `codeburn 0.9.24`
**Loyiha:** `D:\Startups\Agent-paltform`
**Oxirgi yangilanish:** 2026-09-23 22:16 (GMT+5)

---

## 0. O'qish bo'yicha ogohlantirish (o'zgarmas)

Quyidagi qoidalar hisobotning **har bir raqamiga** tegishli va ular keyingi
bo'limlarda takrorlanmaydi:

- **Bayt ≠ token.** codeburn konfig obyektlarining (skil tavsiflari, buyruq
  ta'riflari, MCP tool sxemalari) hajmini ba'zan baytda o'lchaydi, sarfni esa
  tokenda. Ikkisini to'g'ridan-to'g'ri qo'shib bo'lmaydi. "333 bayt" ni
  "333 token" deb o'qish **noto'g'ri**.
- **Estimated ≠ measured.** Topilmalar ikki turga bo'linadi:
  `basis: "estimated"` — modelga asoslangan proyeksiya; `basis: "measured"` —
  sessiya yozuvlaridan o'qilgan haqiqiy son. Faqat **bittasi** (topilma #7 /
  `context-heavy-sessions`) measured. Qolgan oltitasi estimated.
- **Foiz — kafolat emas.** `potentialSavingsPercent` — potensialning sarfga
  nisbati, ya'ni **shift** belgisi. U "shu qadar tejaymiz" degan va'da emas;
  hech qanday holatda bajarilish kafolati sifatida o'qilmasin.
- **Realized raqamlar pastga yaxlitlanadi** va har bir tuzatish **faqat o'z
  ko'rsatkichini** o'lchaydi. Effekt hech qachon bir signaldan ikkinchisiga
  o'tkazilmaydi (attribution yo'q). Guard qatorlari — korrelyatsiya, sabab emas.
- **Oyna konfigdan emas, sessiya tarixidan o'qiladi.** Shuning uchun o'chirilgan
  narsa joriy davrda hali ham ko'rinishi mumkin (pastda §4 ga qarang).

---

## 1. Birinchi o'lchov va qo'llangan o'zgarishlar (2026-09-20)

Birinchi audit 2026-09-20 da o'tkazilgan (`reports/context-audit.md`, shu fayl).
Natija: **F (26/100)** — keyin darhol 3 ta A gradatsiya o'zgarishi qo'llanilgach
**D (41/100)**. Keyingi o'lchov 3 kundan keyin (bugun) belgilangan edi.

| ID | Sana | Nima qilindi | Undo |
|---|---|---|---|
| `c523163b` | 2026-09-20 15:58 | 4 ishlatilmagan Hostinger MCP lokal konfigdan olib tashlandi (hosting, vps, reach, domains). Qoldirildi: dns, billing | `codeburn act undo c523163b` |
| `83afeaea` | 2026-09-20 16:02 | 6 skil `.archived/` ga: brainstorming, domain-modeling, grilling, improve-codebase-architecture, subagent-driven-development, writing-plans | `codeburn act undo 83afeaea` |
| `fe618f24` | 2026-09-20 16:04 | `~/.bashrc` ga `BASH_MAX_OUTPUT_LENGTH=15000` | `codeburn act undo fe618f24` |

**Qo'lda (kod tashqarisida) qilingan ish:** `CLAUDE.md` ga read-edit nisbati
qoidasi qo'shildi (o'lchov 0.7:1 → maqsad 4:1). Fayl 879 → 1139 bayt.

**O'sha kuni qilingan tuzatish:** codeburn `unused-skills` ro'yxatiga
`test-driven-development` va `verification-before-completion` ni ham kiritgan edi.
Ular loyiha `CLAUDE.md` qoidalariga tayanadi — **qo'lda `.archived/` dan
qaytarildi**. Bu `83afeaea` verdiktining "reverted" bo'lishining sababi (§3).

**Backuplar:** `~/.claude.json.backup-20260920-205705`,
`~/.claude/skills-backup-20260920-205706`, `~/.bashrc.backup-20260920-210422`,
`~/CLAUDE.md.backup-*`.

---

## 2. Yangi o'lchov — 2026-09-23 (`codeburn optimize -p week`)

| Ko'rsatkich | 2026-09-20 (birinchi) | **2026-09-23 (yangi)** | Farq |
|---|---|---|---|
| **Salomatlik** | **F (26/100)** → D (41/100, qo'llashdan keyin) | **F (23/100)** | **−3 dan D ga nisbatan** |
| Topilmalar | 5 → keyin 7 | **7** | 0 (D holatiga nisbatan) |
| Sarf | ~$1 376 | **$2 056.69** | +$680 |
| Seanslar | 5 | **17** | +12 |
| Chaqiruvlar | 8 770 | **15 657** | +6 887 |
| Potensial tejash | ~14.3% | **702.9M token ≈ $241.97 (11.8%)** | % pasaydi |
| Shu zahoti qo'llanadigan | — | **47.0M token ≈ $16.18** (4 topilma) | — |
| Bazis | — | 1 measured · 6 estimated | — |

Taqqos oynasi o'zgarganiga e'tibor bering: birinchi auditning "26" i **boshqa
davr** yozuvlaridan olingan edi. Teng oynada solishtirsak:

| Oyna | Sana oralig'i | Salomatlik | Topilma | Sarf | Seans |
|---|---|---|---|---|---|
| O'zgarishdan **oldin** | 2026-09-09 → 09-15 | **D (41)** | 5 | $1 383.03 | 5 |
| O'zgarishdan **keyin** | 2026-09-15 → 09-23 | **F (23)** | 7 | $2 056.69 | 17 |
| Keng oyna | oxirgi 30 kun | **F (22)** | 6 | $11 260.68 | 42 |

Ya'ni **teng oynada solishtirganda ham salomatlik yaxshilanmadi** — D (41) dan
F (23) ga tushdi. Buning sababi "o'zgarishlar ishlamadi" emas; quyidagi
bo'limlar sababni ajratib ko'rsatadi.

---

## 3. Uch o'zgarishning verdikti (`codeburn act report`)

| ID | Topilma | Sana | Estimated | **Realized** | Verdikt | Baho |
|---|---|---|---|---|---|---|
| `c523163b` | `mcp-low-coverage` | 09-20 15:58 | 8 064 000 | **8 064 000** | **worked** ✅ | O'lchandi, va'da baj arildi |
| `83afeaea` | `unused-skills` | 09-20 16:02 | 61 440 | 0 | **reverted** ⚠️ | Foydalanuvchi qaytardi |
| `fe618f24` | `bash-output-cap` | 09-20 16:04 | 3 750 | 0 | **not measurable** ❌ | O'lchab bo'lmaydi |
| | **Jami realized** | | | **8.1M (~$4.16)** | | |

**Batafsil verdiktlar:**

- **`c523163b` — ISHLADI.** `codeburn act report`: "est. 8.1M → measured 8.1M",
  confidence `low`. 4 ta Hostinger MCP server haqiqatan lokal konfigdan
  chiqarilgan. **Qo'lda tasdiqlandi:** `~/.claude.json` da top-level
  `mcpServers` faqat `hostinger-dns` va `hostinger-billing` — ya'ni o'chirilgan
  4 tasi qaytmagan. (Konfigda 6 ta loyiha bor, hech birida lokal MCP yo'q.)
  Realized raqam per-sessiya bazisi × seans sonidan **hosil qilingan**, mustaqil
  o'lchanmagan — confidence shuning uchun `low`.

- **`83afeaea` — QAYTARILDI (foydalanuvchi qarori).** `codeburn act report` sababi:
  *"an archived item was moved back into place"*. Bu **men `test-driven-development`
  va `verification-before-completion` ni qo'lda tiklaganim** (§1). Ya'ni
  texnik nosozlik emas, **ataylab qilingan qaytarish**: qolgan 4 skil
  (codebase-design, find-skill, skill-creator, frontend-design) arxivda qoldi,
  ikkitasi qaytdi. codeburn butun amalni "reverted" deb belgiladi.
  **Narxi o'lchandi:** bu topilmaning butun tejamkorligi ~400 token — bir o'zgarish
  uchun bu shovqin darajasida (estimated $0.0001).

- **`fe618f24` — O'LCHAB BO'LMAYDI.** `codeburn act report`: *"bash result token
  sizes are not retained in the summary"*. Sabab — asbob cheklovi: bash
  natijalarining token hajmi jamlanmada saqlanmaydi, shuning uchun "oldin/keyin"
  farqini chiqarib bo'lmaydi. **Konfiguratsiya o'zi joyida:** `~/.bashrc` da
  `# codeburn:begin/end bash-output-cap` markerlari bilan
  `export BASH_MAX_OUTPUT_LENGTH=15000` o'rnatilgan (fayl atigi 3 qator).
  Ya'ni "ishlamadi" emas — "o'lchash imkoni yo'q".

---

## 4. Nima uchun MCP topilmasi hali ham ro'yxatda

`mcp-low-coverage` bugungi hisobotda **"previously applied 2026-09-20,
re-flagged"** deb belgilangan holda yana birinchi o'rinda turibdi. Bu
**regressiya emas**.

codeburn sessiyalar tarixini o'qiydi, joriy konfiguratsiyani emas. Bugungi
haftalik oynaning katta qismi **o'chirishdan oldin** yozilgan yozuvlardan
iborat (xawardagi 118 / 22 seanslik raqamlar tarixiy). Shuning uchun:

- **Lokal** 4 ta Hostinger server: konfigdan **chindan chiqarilgan** (qo'lda
  tasdiqlandi) — bu raqam keyingi oynalarda tabiiy ravishda tushadi.
- **claude.ai connectorlari** (higgsfield, Gmail, OpenArt): `~/.claude.json` →
  `claudeAiMcpEverConnected` da 11 ta yozuv bor, lekin ular **lokal faylda YO'Q**.
  Ular claude.ai **hisobi (server) tomonda** boshqariladi — na `--apply`, na
  qo'lda skript ularni o'chira oladi. Faqat: Claude Settings > **Connectors**.
  codeburn o'zi ham buni aytadi: *"separate from any similarly named local MCP
  server"*.

**Xulosa:** bu topilmaning turaverishi o'lchash oynasining xossasi + boshqarib
bo'lmaydigan claude.ai qatlami. Uni "topilma yo'qoldimi?" bilan baholash
mumkin emas — §3 dagi realized son va §5 dagi salomatlik bilan baholanadi.

---

## 5. Yangi topilmalar to'liq ro'yxati (7 ta)

| # | ID | Daraja | Basis | Token | Est. $ | Izoh |
|---|---|---|---|---|---|---|
| 1 | `mcp-low-coverage` | high | estimated | 153.6M | 52.86 | 7 server; 4 tasi lokal allaqachon olib tashlangan, 3 tasi claude.ai |
| 2 | `read-edit-ratio` | high | estimated | 4.28M | 1.47 | **1514 read / 2163 edit = 0.7:1**, trend `active` |
| 3 | `unused-skills` | medium | estimated | 400 | 0.0001 | 5 skil; TDD/verification qaytarilgani uchun ro'yxatda |
| 4 | `unused-commands` | low | estimated | 60 | 0.00002 | `install-skill.md` |
| 5 | `low-worth-sessions` | high | estimated | 99.1M | 34.10 | `d--Startups-BV-AI/fcd3b940` 2026-09-21: $276.13, 21 retry |
| 6 | `redundant-rereads` | medium | estimated | 9.0K | 0.0031 | 15 takroriy o'qish; `chunking.service.ts` 3x, `batch.service.ts` 3x |
| 7 | `context-heavy-sessions` | high | **measured** | 446.0M | **153.53** | 13 seans; eng yomoni 453.8:1 va 543.3:1 |

**Yangi paydo bo'lganlar (birinchi auditda yo'q edi):**
`unused-commands`, `redundant-rereads`, `low-worth-sessions` (birinchi oynada
o'rtada, keyin medium→high bo'ldi). Hammasi **estimated**.

**"Fix now" (qo'llanadigan) bloki:** 4 topilma, ~47.0M token ≈ **$16.18** —
ya'ni 7 topilmaning umumiy potensiali $241.97 bo'lsa, **faqat $16.18** i
avtomatik qo'llanadi. Qolgani `keep` sinfida (odat o'zgarishi talab qiladi).

---

## 6. Nima qolgan (harakat talab qiladigan)

1. **claude.ai connectorlari** — higgsfield (104 tool, 0%), Gmail (30, 0%),
   OpenArt (17, 0%). Lokal faylda yo'q. **Qo'lda:** Claude Settings >
   Connectors. Eng katta bitta potensial (~153.6M token estimated).
2. **`context-heavy-sessions` — yagona measured topilma va eng qimmatlisi
   ($153.53).** Sabab: eski kontekst ko'chirilishi va tashlab ketilgan
   yurishlar. Chora — **odat**, konfig emas: har qimmat mavzuni "hozirgi maqsad +
   tegishli fayllar + yiqilgan buyruq chiqishi" bilan yangidan boshlash.
   `codeburn` tayyor session-opener matnini beradi (CLAUDE.md ga **emas**,
   bir martalik).
3. **`read-edit-ratio` 0.7:1 — trend `active`, ya'ni yomonlashmayapti, lekin
   yaxshilanmayapti ham.** Qo'lda qo'shilgan CLAUDE.md qoidasi kutilgan effektni
   bermadi (§7). Ehtimol qoida yetarli aniq emas: "faylni o'qimasdan tahrirlama"
   dan ko'ra aniqroq — *quyi oqimni bir marta o'qib, keyin bir necha tahrir*.
4. **`low-worth-sessions`** — BV AI loyihasidagi bitta $276.13 seans, 21 retry.
   Ko'rib chiqish nomzodi, isrof isboti emas. Retry siyosatini seans ochilishida
   cheklash tavsiya etiladi.
5. **`tests/` taqdirini hal qilish** (`api-python/tests/` — 33 qizil, gatesiz).
6. **Node runner Windows bloklangan yuzasi** (11/24) — bu codeburn emas, loyiha
   auditi qatori; eslatma uchun qoldirildi.

---

## 7. Verdikt va saboqlar

**Salomatlik o'zgardimi?** Ha, lekin **yaxshi tomonga emas**: o'zgarishdan oldingi
teng oyna D (41) → keyingi oyna F (23). Sabablari ajratilgan:

- Bu **o'zgarishlarning muvaffaqiyatsizligi emas**. Uchta o'zgarishning umumiy
  haqiqiy tejamkorligi bor-yo'g'i **8.1M token (~$4.16)** — bu $2 056 sarflagan
  oynada **0.2%**. Salomatlik ko'rsatkichi butunlay boshqa omillar bilan
  harakatlanadi (kontekst og'irligi, retry, sarf hajmi).
- Oynada **seans 5 → 17**, sarf $1 383 → $2 056 bo'ldi. Ya'ni ish hajmi o'sdi,
  va o'sish kontekst-og'ir tomon ketdi (`context-heavy-sessions` $153.53).
  Salomatlik darajasi **sarf tarkibini** o'lchaydi, sarfni emas — shuning uchun
  katta kontekstli seanslar ulushi oshsa, daraja tushadi.

**Qaysi o'zgarish ishladi?** Aniq bittasi: **`c523163b` (MCP)** — realized
8.1M = estimated 8.1M, qo'lda tasdiqlangan. Qolgan ikkisi o'lchanmadi:
`83afeaea` ataylab qaytarilgan (arzon, ~400 token — yo'qotish kichik),
`fe618f24` asbob cheklovi tufayli o'lchab bo'lmaydi (konfiguratsiya joyida).

**Asosiy saboq:** konfig-darajali "A gradatsiya" o'zgarishlar **katta oynada
shovqin darajasida**. 8.1M token 47M tokenlik "fix now" blokining ~17% i, lekin
butun oynaning 0.2% i. Salomatlikni ko'tarish uchun konfig emas, **xulq**
kerak: (a) claude.ai connectorlarini tozalash (yagona yirik konfig yutug'i
qolgan), (b) kontekst-og'ir seanslarni yangidan boshlash odati, (c) read-edit
nisbatini aniqroq qoida bilan ushlash.

**Narxni kelajakda o'lchash uchun:** keyingi qayta o'lchovda **teng uzunlikdagi
oyna** ishlatilsin (`--from/--to` bilan aniq oraliq), aks holda "26 → 41 → 23"
kabi raqamlar turli davrlarni solishtirib yuboradi.

---

### Reproduksiya buyruqlari

```bash
export PATH="/c/Users/Sunnatulloh/.workbuddy-ai/binaries/node/versions/22.22.2-2:$PATH"
cd /d/Startups/Agent-paltform

codeburn act report                       # realized vs estimated (3 kundan keyin)
codeburn act list                         # qo'llangan amallar + ID lar
codeburn optimize -p week --format json   # haftalik topilmalar
codeburn optimize --from 2026-09-09 --to 2026-09-15 --format json   # bazis oyna
codeburn optimize -p 30days --format json # keng oyna
```

Bekor qilish: `codeburn act undo c523163b` | `83afeaea` | `fe618f24`

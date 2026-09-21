# PRD + Texnik Topshiriq (TZ)
## Universal Agentic Platform — "har qanday biznes uchun AI xodimlar"

Versiya 0.4 · 2026-09-12 · Muallif: Sunnatullo Hayitov
O'zgarish: v0.4 — JARVIS PDF tahlili (§11), BYOS siyosati (shaxsiy vs biznes ajratish), Obsidian→pack mapping, CLAUDE.md xavfsizlik importi.

---

## 0. Bir sahifada mohiyati

**Mahsulot:** bitta core tizim + mijozga xos "pack" + mijoz kompyuterida turadigan Local Runner. Mijoz o'z kompaniyasining AI xodimlar xaritasini ko'radi (SkillTree uslubi), ular bilan matn/ovoz orqali gaplashadi (APEX uslubi), agentlar real tizimlarga (Telegram, Gmail, Sheets, CRM, 1C) va real qurilmalarga (PC dasturlari, ekran, printer, IoT, ishlab chiqarish uskunalari) ulanib ishni bajaradi.

**Asosiy tamoyillar (o'zgarmaydi):**
1. **Core bir marta yoziladi, pack config'da yashaydi.** Yangi mijoz = yangi YAML + kerakli MCP serverlar. Kod o'zgarishi ≤ 20%.
2. **Har agent tool'ga bog'langan.** Tool'siz agent — bu chat, mahsulot emas.
3. **Ladder:** har agent uchun avtonomiya darajasi (Human-led → Human-assisted → Autonomous). Mijoz o'zi ko'taradi.
4. **Tool ustuvorligi:** API → CLI/script/COM → brauzer avtomatizatsiya → ekran. Ekran — oxirgi chora.
5. **O'zbek tili birinchi.** Lotin/kirill, shevalar, rus aralash matn tushuniladi; javob standart o'zbek tilida (yoki mijoz tanlagan uslubda).
6. **Kam, lekin ishlaydigan.** Birinchi pack'da 8–10 ta real, testlangan agent.
7. **Real dunyoda harakat = tasdiq bilan.** Yuborish, o'chirish, chop etish, to'lov, uskuna boshqaruvi default tasdiq talab qiladi.
8. **Miya cloud'da, qo'l mijozda.** LLM kalitlari va orkestrator faqat bizning cloud'da. Runner'da aql yo'q — faqat tool ijrosi. Runner 24 soatlik JWT + 30 sek heartbeat bilan ishlaydi; obuna to'xtasa yoki 7 kun offline bo'lsa — freeze. Pack.yaml HMAC imzo bilan tasdiqlanadi. Bu nusxalashdan himoya va oylik billing asosi.

---

## 1. Muammo va maqsad

O'zbekiston SMB'larida (o'quv markazlari, marketing agentliklar, buxgalteriya outsourcing, savdo/dilerlik, ishlab chiqarish) takrorlanuvchi ishlar ko'p: mijozga javob, hisobot yig'ish, lead kuzatish, hujjat tayyorlash, 1C/Excel'da ma'lumot kiritish, chop etish, ombor/uskuna holatini kuzatish. Bu ishlar Telegram + Excel + 1C + eski Windows dasturlari atrofida aylanadi, tizimlashmagan, odamga bog'liq.

**Maqsad:** 2–4 hafta ichida istalgan SMB'ga uning ishlarini bajaradigan — ma'lumot bilan ham, dastur va qurilmalar bilan ham — o'zbekcha gaplashadigan AI xodimlar to'plamini o'rnatib berish.

**Muvaffaqiyat mezonlari (6 oy):**
- 3 ta pilot mijoz, 2 ta pullik
- Yangi mijozni o'rnatish: ≤ 10 ish kuni
- Agent vazifalari muvaffaqiyati (test to'plamida): ≥ 90%; ekran-asosli vazifalarda ≥ 80%
- Mijoz haftalik faol foydalanish: ≥ 5 kun/hafta
- Runner sababli xavfsizlik hodisasi: 0

---

## 2. Foydalanuvchilar

| Rol | Kim | Nima qiladi |
|---|---|---|
| Owner | Direktor / egasi | Xaritani ko'radi, ladder darajasini boshqaradi, runner ruxsatlarini beradi, hisobot oladi |
| Operator | Xodim (admin, menejer, buxgalter) | Agentlar bilan kundalik ishlaydi, tasdiqlaydi, kill switch |
| Integrator (siz) | Platforma egasi | Pack yaratadi, MCP/runner ulaydi, monitoring |
| Super-admin | Siz | Tenant'lar, billing, model kalitlari |

---

## 3. Funksional talablar

### 3.1 Core (barcha mijozlar uchun bir xil)

**F1. Orkestrator**
- Kiruvchi so'rov (matn/ovoz/webhook/cron/qurilma hodisasi) → niyat aniqlash → agentga yo'naltirish → natija.
- Ko'p bosqichli vazifalar: reja → qadamlar → tool chaqiruvlar → tekshiruv → hisobot.
- Har qadam log'lanadi (kim, qachon, qaysi tool, natija, xarajat, screenshot bo'lsa).
- Retry, timeout, xato bo'lganda odamga eskalatsiya.
- Tool tanlashda ustuvorlik qoidasi majburiy (§3.4).

**F2. Agent modeli** — YAML ta'rif:
```yaml
id: backoffice.invoice_entry
name: "1C'ga hisob-faktura kirituvchi"
department: back_office
persona: prompts/backoffice/invoice_entry.md
tools: [files.read_pdf, onec.http.create_invoice, runner.screen]   # runner.screen — fallback
tool_policy: prefer_api                                            # api > cli > browser > screen
ladder: human_assisted
approval: { required_for: [onec.http.create_invoice, runner.screen.*], approver_role: operator }
memory: { scope: tenant, collections: [suppliers, products] }
triggers: [{ type: webhook, source: gmail.new_attachment, filter: "pdf" }]
tests: tests/backoffice/invoice_entry/*.yaml
language: { input: any, output: uz-latn, tone: rasmiy }
```

**F3. Tool qatlami — MCP**
Har integratsiya alohida MCP server. Uch sinf:

*Cloud MCP (siz hosting qilasiz):*
- v1: Telegram Bot, Gmail, Google Sheets/Drive/Calendar, ichki CRM (Postgres), webhook/HTTP, fayl (PDF/DOCX/XLSX o'qish-yozish)
- v2: amoCRM/Bitrix24, Meta/Google Ads, Payme/Click hisobot, Soliq.uz (faqat o'qish, API bo'lsa)

*Runner MCP (mijoz qurilmasida, §3.5):*
- Dasturlar, ekran, fayl tizimi, printer/skaner, IoT, sanoat qurilmalari

*Tashqi tayyor MCP (olib ulanadi):*
- Playwright MCP (brauzer), Home Assistant MCP (IoT), Filesystem MCP

Har tool: input/output schema, ruxsat darajasi (`read` / `write` / `destructive` / `physical`), rate limit, tenant allowlist.

**F4. Xotira**
- Tenant bilimlar bazasi: hujjatlar → chunk → embedding → Supabase pgvector (MVP; tenant per collection). Qdrant'ga ajratish faqat KB katta bo'lsa (v2).
- Suhbat xotirasi: Redis. Faktlar/holat: Postgres.
- Qurilma holati (oxirgi ko'rilgan ekran, sensor qiymatlari): Redis, TTL bilan.
- Har agent faqat o'z `memory.scope` ichida ko'radi.

**F5. Ladder va tasdiq oqimi**
- `human_led`: agent taklif/qoralama beradi, odam bajaradi.
- `human_assisted`: agent bajaradi, `approval.required_for` tool'lar oldidan tasdiq (Telegram tugma / UI / runner'da popup).
- `autonomous`: mustaqil, faqat log.
- Owner darajani UI'da o'zgartiradi. Ko'tarish sharti: oxirgi 30 vazifada xato ≤ 5%.
- `destructive` va `physical` tool'lar `autonomous` bo'lishi uchun Owner alohida "men tushundim" tasdig'i.

**F6. Til qatlami (o'zbek tili)**
- Normalizatsiya: kirill ↔ lotin, apostrof variantlari (o‘/o'/oʻ), rus-o'zbek aralash so'zlar.
- Sheva lug'ati: Toshkent, Farg'ona vodiysi, Xorazm, Surxondaryo/Qashqadaryo, Buxoro/Samarqand ("kelvotti/kelyapti", "bormisan/bormisiz", "hozi/hozir", "nima gap"). System prompt'da few-shot, tenant kengaytiradi (`dialect.yaml`).
- Chiqish: standart adabiy o'zbek (lotin) default; kirill yoki so'zlashuv uslubi tanlanadi.
- Model: Claude asosiy. Sheva testlari eval'da majburiy (≥ 50 misol).
- Ovoz (v2): o'zbek STT/TTS provayderlari sinovi (Mohir AI, Aisha AI, Whisper large-v3). WER ≤ 15% bo'lmasa ovoz ishga tushmaydi.

**F7. Kanallar**
- Telegram bot (asosiy), Web UI (xarita + chat + vazifalar), Runner tray ilovasi (holat + kill switch)
- v2: ovoz (web + runner mikrofon), WhatsApp

**F8. Test va eval**
- Har agent `tests/*.yaml`: input, kutilgan tool chaqiruvlar, natija mezonlari.
- Ekran/dastur testlari: sandbox VM'da (Windows 11 image) CI'da ishlaydi, screenshot diff bilan.
- Natija < 90% (ekranda < 80%) bo'lsa deploy bloklanadi.
- Prod: har 100 vazifadan 5 tasi odam tekshiruviga.

### 3.2 UI/UX

**U1. Agent xaritasi** — radial graf: markazda kompaniya, bo'limlar, agentlar, va **qurilma tugunlari** (PC-1, Printer, Ombor kamerasi, Stanok-3). Holat ranglari: ishlayapti / kutmoqda / tasdiq kerak / xato / offline / rejalashtirilgan.

**U2. Agent sahifasi:** nima qiladi, nimani almashtiradi, ladder (+ ko'tarish), so'nggi 20 vazifa (screenshot bilan bo'lsa), testlar natijasi, ulangan tool'lar va qurilmalar.

**U3. Qurilma sahifasi:** runner holati (online/offline, versiya), ruxsat berilgan dasturlar/papkalar ro'yxati, jonli ekran preview (faqat Owner, faqat vazifa paytida), so'nggi harakatlar.

**U4. Buyruq paneli** (APEX): bitta input. "Bu haftadagi leadlar", "shu PDF'ni 1C'ga kirit", "ombordagi 2-kamerani ko'rsat", "hisobotni chop et". Natija chatda + tegishli tugunlar yonadi.

**U5. Tasdiq navbati** — kutayotgan harakatlar; ekran harakatlari uchun "agent nima qilmoqchi" screenshot + izoh bilan. Tasdiqla / rad et / o'zgartir.

**U6. Hisobot** — haftalik: bajarilgan vazifalar, tejalgan vaqt, xarajat, xatolar, runner uptime.

**U7. Sozlanuvchanlik (3 daraja):** tema (`theme.json`, white-label), layout (`radial_map` | `department_board` | `chat_first`), tarkib (pack'dan).
- `radial_map` — APEX-UI uslubi (markaziy orb + atrofida agent tugunlari). Faqat vizual referens sifatida olinadi (`ReasoningWeb.ROSTER` → `pack.yaml` ga map qilinadi; `ShaderBackground` tashlanadi — GPU og'ir, biznesga keraksiz). MIT litsenziya, `Apex` brendi ishlatilmaydi.
- Layout switch header'da 1 klik bilan almashadi, user tanlovi Postgres'da saqlanadi. Har widget (`AgentMap`, `CommandBar`, `ApprovalQueue`, `MapsWidget`, `VoiceOrb`) mustaqil.
- `MapsWidget` — mijoz nuqtalari (Yandex Maps ustuvor O'Z uchun, Google fallback). Agent manzil yozadi → widget nuqta chizadi.

**U8. Til:** UI o'zbek (lotin/kirill), rus.

### 3.3 Pack (mijozga xos qism)

```
packs/<client>/
  pack.yaml          # meta: nom, soha, til, layout, tema
  agents/*.yaml
  prompts/**/*.md
  tools.yaml         # cloud MCP'lar + kalitlar
  devices.yaml       # runner'lar, har birining allowlist'i (§3.5)
  knowledge/
  dialect.yaml
  tests/**/*.yaml
```

Pack yaratish: 1) 2 soatlik intervyu (kim nima qiladi, qaysi tizim/qurilmalar) → 2) shablon pack nusxasi → 3) 8–10 agent → 4) tool kalitlari → 5) runner o'rnatish + allowlist → 6) KB → 7) testlar → 8) `human_led` ishga tushirish → 9) 2 hafta kuzatish → 10) ladder ko'tarish.

### 3.4 Tool ustuvorlik qoidasi (majburiy)

Orkestrator har vazifa uchun quyidagi tartibda tool tanlaydi; pastroq darajaga faqat yuqorisi mavjud bo'lmasa tushadi va buni log'ga yozadi:

| Daraja | Vosita | Tezlik | Ishonchlilik | Misol |
|---|---|---|---|---|
| 1 | API / MCP cloud | ~1 s | 99% | Sheets, Gmail, 1C HTTP-service |
| 2 | CLI / script / COM / AppleScript | ~1–3 s | 95% | Excel COM, PowerShell, 1C COM-connector |
| 3 | Brauzer avtomatizatsiya (Playwright) | ~3–10 s | 90% | Soliq.uz kabinet, bank-klient |
| 4 | Ekran (computer use) | 5–20 s/harakat | 75–85% | API'siz eski Windows dasturi |

Sabab: 4-daraja qimmat (har qadam screenshot token), sekin, oyna o'zgarsa adashadi. U "long tail"ni yopadi va demo'da ta'sirli, lekin asosiy yuk ko'tarmasligi kerak.

### 3.5 Local Runner (yangi komponent)

**Nima:** mijoz qurilmasiga o'rnatiladigan daemon (Node + Electron tray; Windows/macOS/Linux). Cloud core'ga outbound WebSocket (mijoz tomonida port ochilmaydi), o'zini MCP server sifatida taqdim etadi. Tenant tokeni bilan autentifikatsiya.

**Tool guruhlari:**

| Guruh | Tool'lar | Texnologiya |
|---|---|---|
| `runner.screen` | screenshot, click, type, scroll, find_window, focus | Anthropic Computer Use tool + nut.js/robotjs |
| `runner.browser` | open, fill, click, extract | Playwright (headed, mijoz brauzeri profili bilan) |
| `runner.app` | launch, close, run_script, send_keys | PowerShell/AutoHotkey (Win), AppleScript/Shortcuts (mac), bash (Linux) |
| `runner.office` | excel.read/write, word.fill | COM (Win) / docx-xlsx kutubxonalar |
| `runner.onec` | query, create_doc | 1C COM-connector yoki HTTP-service |
| `runner.fs` | list, read, write, move | Faqat allowlist papkalar |
| `runner.print` | print_file, printer_status | CUPS / Windows print API |
| `runner.scan` | scan_document, read_barcode | TWAIN/WIA, serial/HID skaner |
| `runner.camera` | snapshot, stream_url | RTSP/ONVIF |
| `runner.iot` | get_state, set_state | Home Assistant MCP (MQTT/Zigbee/Wi-Fi) |
| `runner.industrial` | read_register, write_register | Modbus TCP/RTU, OPC-UA (v2) |
| `runner.gpio` | read, write | Raspberry Pi (v2) |

**Ruxsat modeli (devices.yaml):**
```yaml
runners:
  - id: office-pc-1
    os: windows
    allow:
      apps: ["1cv8.exe", "EXCEL.EXE", "chrome.exe"]
      folders: ["C:/Hisobotlar", "D:/Skan"]
      printers: ["HP-Office"]
      screen: { enabled: true, windows: ["1С:Предприятие", "Excel"] }
    deny_always: ["parol", "karta", "bank-klient"]   # oyna sarlavhasi bo'yicha
    physical: ["HP-Office"]                          # physical sinf → doim tasdiq
```

**Xavfsizlik talablari:**
- Allowlist tashqarisidagi hamma narsa rad etiladi, log'lanadi.
- Parol/karta/CVV maydonlariga hech qachon yozilmaydi (maydon turi + oyna nomi bo'yicha aniqlanadi); login mijoz o'zi qiladi.
- `destructive`/`physical` harakatlar default tasdiqda.
- **Kill switch:** tray tugmasi + global hotkey (Ctrl+Shift+Esc×2) + Telegram'da "/stop" → runner shu zahoti barcha harakatni to'xtatadi va cloud'ga xabar beradi.
- Har ekran harakati oldidan/keyin screenshot audit'ga (mijoz serverida yoki cloud'da, tenant tanlaydi; 90 kun).
- Runner faqat foydalanuvchi sessiyasi ochiq bo'lganda ishlaydi; qulflangan ekranda ochmaydi.
- Yangilanish: imzolangan release'lar, avtomatik, rollback bilan.
- Runner offline bo'lsa: vazifa navbatda kutadi, 1 soatdan keyin operatorga xabar.

**24/7 rejim:** ishlab chiqarish/ombor uchun runner arzon mini-PC yoki Raspberry Pi'ga o'rnatiladi ("runner host"). Bu apparat sotish emas — runner joylashadigan joy; mijoz o'zi sotib oladi. Kichik kompaniya uchun ideal: **Mac mini** (kam tok, always-on, printer/1C/Excel ga USB/LAN). Runner'da LLM kaliti va DB yo'q — faqat tool ijrosi, shuning uchun Mac mini kuchli server bo'lishi shart emas.

**Litsenziya/billing bog'lash (nusxadan himoya):**
- Runner cloud'ga tenant token + 24 soatlik JWT bilan ulanadi, har 30 sek heartbeat.
- Barcha LLM chaqiruvlar cloud gateway orqali (kalitlar vault'da, mijozda yo'q). Token limit Postgres'da, 80% da ogohlantirish.
- `pack.yaml` HMAC imzo bilan keladi; imzo mos kelmasa runner ishlamaydi (boshqa firmaga ko'chirib bo'lmaydi).
- Offline > 7 kun yoki obuna to'xtasa — runner freeze (miya o'chadi, qo'l qoladi).

---

## 4. Nofunksional talablar

| Talab | Qiymat |
|---|---|
| Javob vaqti (oddiy) | ≤ 5 s; ko'p bosqichli ≤ 60 s; ekran vazifasi ≤ 5 daq, progress ko'rsatiladi |
| Mavjudlik | Cloud 99.5%; runner — mijoz qurilmasiga bog'liq |
| Izolyatsiya | Tenant ID barcha jadval/collection'da; row-level security; runner tokeni tenant'ga bog'langan |
| Maxfiylik | Ma'lumot model treningiga bormaydi; kalitlar vault'da; screenshot'lar shifrlangan |
| Xarajat | Tenant oylik token limiti; ekran vazifalari alohida limit; 80% da ogohlantirish |
| Audit | Tool chaqiruvlari 12 oy, screenshot'lar 90 kun |
| Kuzatuv | Langfuse/OpenTelemetry — har run trace, runner metrikalari |
| Runner resurs | ≤ 300 MB RAM, ≤ 5% CPU bo'sh holatda |

---

## 5. Arxitektura va stack

```
[Telegram] [Instagram DM] [Web UI — Next.js] [Webhook/Cron] [Runner hodisalari]
       \          |               |              /                /
               [API Gateway — FastAPI (Python, gibrid qaror §5)]
                       |
      [Orchestrator — LangGraph Python / Anthropic Agent SDK]
        |           |            |            |
  [Agent runtime] [Memory]  [Approval]  [Tool router — ustuvorlik §3.4]
        |                                     |
  [MCP client] ─── cloud MCP: telegram (grammY), instagram (Graph API), gmail, sheets, calendar, crm, files
               ─── tashqi MCP: playwright, home-assistant (openhuman bilan faqat MCP/webhook chegarasi)
               ─── WebSocket (JWT 24s + heartbeat 30s) ─── [Local Runner @ mijoz Mac mini/PC: Electron/Tauri (TS) + screen/app/office/1C/fs/print/scan/camera/iot/modbus]
               ─── ML sidecar (Python, faqat kerak bo'lsa): faster-whisper STT, embedding/RAG worker — alohida kichik servis
        |
  [LLM: Claude Haiku 3.5 asosiy (arzon) + Sonnet murakkabga | Groq Llama dev/fallback | on-prem model faqat premium]

  Data (MVP): Supabase Postgres + pgvector (alohida Qdrant yo'q) · Upstash Redis · Object storage (screenshot 90 kun)
  Data (v2): Qdrant ajratish faqat KB katta bo'lsa
  Infra: cloud — Railway/Render ($5-20/oy) → k8s/Fly.io yoki O'Z data-markazi (keyin); runner — Electron/Tauri auto-update, code signing
  Kuzatuv: Langfuse (bepul tier) · Sentry · CI: GitHub Actions (+ Windows VM ekran testlari)
```

**Stack qarori (v0.4, yakuniy): gibrid — miya Python, yuz+qo'l TS.** Sabab: egasiga farqi yo'q + vibe coding'da til to'siq emas. AI misollar (LangGraph, Whisper, RAG) Python'da pishgan — brain shu tilda tez chiqadi; UI (Next.js) va Runner (Electron) baribir TS bo'ladi. Chegara toza: REST/WebSocket, `packages/packs` YAML ikkala tomonda zod/pydantic bilan validatsiyalanadi. API: FastAPI (solo uchun NestJS'dan sodda). Python tajribasi — shu loyihada keladi, risk yo'q (API-glue, ML-training emas).

**Deploy modeli — gibrid:**
- **Standart:** core multi-tenant cloud + runner mijozda. Bir marta yangilash, bir joyda monitoring.
- **On-prem (premium, 2–3× narx):** xuddi shu Docker Compose mijoz serverida; LLM baribir API orqali. Local model (Qwen/Llama) faqat maxsus shartnomada — o'zbek tili sifati va tool-use ishonchliligi pastroq, bu mijozga aytiladi.
- Shaxsiy ma'lumotlar qonuni: cloud'ni O'zbekiston hostingida joylash ehtimoli — yuridik tekshiruv.

---

## 6. Bosqichlar (v0.4 — kontakt'siz, sotuv-birinchi)

| Bosqich | Muddat (kechqurun) | Natija (qabul mezoni) |
|---|---|---|
| 0. Skelet | 3-4 kun | Gibrid repo (`api-python/ apps/ui apps/runner packs/_template/`), FastAPI hello + Next.js hello, Supabase auth+tenant, JWT chiqarish. Mezon: `GET /health` + tenant ajraladi |
| 1. Sotuv-MVP (demo retail, text) | 2 hafta | Telegram bot: 6 sotuv agenti, 20 tovar, 2 filial, buyurtma → Sheets. Mezon: 10 test ssenariy 9 tasi o'tadi, javob ≤5 sek |
| 2. Ladder + tasdiq + eval | 1 hafta | Approval navbati (Telegram tugma), 30 test, log+Langfuse. Mezon: pul/chop etish tasdiqsiz o'tmaydi |
| 3. Til qatlami (minimal) | 3-4 kun | Lotin/kirill normalizatsiya, 30 sheva testi, FAQ KB. Mezon: aralash matnda 90% |
| 4. IG DM + 2-vertikal template | 1.5 hafta | Instagram webhook + marketing pack (4 agent). Mezon: DM 30 sek ichida, yangi pack 3 kunda |
| 5. UI v1 (minimal) | 1.5 hafta | Buyruq paneli + buyurtmalar ro'yxati + tasdiq tugmasi (radial map statik). Mezon: sotuvchi brauzerdan tasdiqlaydi |
| 6. Runner v1 (fayl/ofis) | 2 hafta | Tray + WebSocket + `fs/app/office/print`, allowlist, kill switch, read-only audit birinchi. Mezon: Excel/print testda |
| 7. Demo + birinchi sotuv | 2 hafta | Demo video 3 daq + 10 do'kon suhbat + 1 pullik pilot ($300+$150). Mezon: pul tushdi |
| 8. Ovoz/IoT/1C (v2, alohida narx) | 4-6 hafta | Voice pilot 1 raqam + buxgalteriya/zavod pack'lari. Mezon: alohida shartnoma bilan |
| **MVP (0–7)** | **~10-12 hafta kechqurun** | sotuvga tayyor demo + 1 pullik pilot, tannarx $30-90/oy |

> **Holat (2026-09-13, kod fakti):** Bu PRD to'liq mahsulot yo'nalishini tasvirlaydi; amaldagi kod esa boshlang'ich baseline.
> Python API testlari: 146 passed, 4 skipped; til testlari: 50 ta; UI typecheck/build o'tgan;
> runner self-test: 19 ta. Hozir SQLite/WAL, rule-based responder, approval, CSV outbox,
> webhook parserlari, operator UI va runner tool self-test mavjud.
> Real Telegram outbound, Instagram outbound, Google Sheets, Claude/LLM, RAG, voice,
> to'liq runner process integration, billing va production staging hali isbotlanmagan.
> Endi qismlarni alohida release qilmaymiz: avval to'liq platforma master-plan bo'yicha quriladi,
> keyin yagona umumiy test, staging va real pilot o'tkaziladi.

---

## 7. Xavflar

| Xavf | Ehtimol | Chora |
|---|---|---|
| Ekran boshqaruvi ishonchsiz/sekin | Yuqori | Ustuvorlik qoidasi; ekran faqat fallback; test ≥ 80% |
| Runner mijoz PC'ida zarar keltiradi | O'rta | Allowlist, deny_always, physical/destructive tasdiq, kill switch, audit |
| O'zbek STT sifati past | Yuqori | Ovoz v2; matn asosiy |
| 1C integratsiyasi og'ir | Yuqori | Avval Excel export; COM/HTTP v2 |
| Mijoz AI'ga ishonmaydi | O'rta | human_led start, screenshot'li tasdiq, haftalik hisobot |
| Token xarajati | O'rta | Limitlar, Haiku/Sonnet routing, cache, ekran vazifalarini kamaytirish |
| Windows versiyalar/antivirus runner'ni bloklaydi | O'rta | Code signing, o'rnatish yo'riqnomasi, pilotda tekshirish |
| Bir kishi hamma ishni qiladi | Yuqori | Scope qattiq; runner v1 faqat 4 tool; 1 soha |

---

## 8. Birinchi pack'lar

### 8.0 Pack 0 — Demo Retail (Turkish Baby kloni, kontakt'siz, hafta 1-4)
Holat (v0.4): real mijoz kontakti yo'q. Shuning uchun Pack 0 real shartnomaga emas, **sotuvga tayyor demoga** quriladi: Turkish Baby'ga o'xshash 2 filialli kiyim do'koni (demo tovarlar, demo narxlar, demo filiallar). Tayyor bo'lgach Turkish Baby'ga ham, boshqa kiyim do'koni / o'quv markazi / restoranga ham 2-3 kunlik sozlash bilan ko'rsatiladi. Birinchi pullik mijoz kim bo'lsa — o'sha Pack 0 ga aylanadi.

| Agent | Bo'lim | Tool'lar | Ladder start |
|---|---|---|---|
| Instagram DM javobchi | Sales | instagram, crm | assisted |
| Telegram buyurtma qabulchi | Sales | telegram, sheets, crm | assisted |
| Filial router | Ops | telegram, sheets | assisted |
| FAQ (ish vaqti, manzil, qaytarish) | Support | kb, telegram, instagram | autonomous |
| Hot lead xabarchi | Sales | crm → telegram (xodimga) | autonomous |
| Kunlik statistika | Intelligence | sheets, telegram | autonomous |

Narx (v0.4 tuzatish — §11.5 ga qarang): text-MVP tannarxi $20-70/oy (3000 suhbat, prompt cache bilan). RAG/CRM qo'shilsa +$10-20. Ovoz (call-center) alohida: 1 daqiqa ~$0.05-0.12 (STT+LLM+TTS+telefoniya), 1000 qo'ng'iroq (3 daq) = $150-360/oy. Shuning uchun ovoz faqat v2, faqat hot-lead pilotda. Mijozga: setup $500-1500 + text retainer $150-300/oy + voice alohida $200-500/oy.

### 8.1 Birinchi pack shabloni (o'quv markazi, Pack 1)

| Agent | Bo'lim | Tool'lar | Ladder start |
|---|---|---|---|
| Lead javob beruvchi | Sales | telegram, crm | assisted |
| Sinov darsiga yozuvchi | Sales | calendar, crm, telegram | assisted |
| To'lov eslatuvchi | Back office | sheets, telegram | led |
| Davomat hisobotchi | Ops | sheets | autonomous |
| Ota-onaga haftalik xabar | Customer | sheets, telegram | assisted |
| Kontent yozuvchi | Marketing | kb, telegram | led |
| Sharh/shikoyat kuzatuvchi | Customer | telegram, crm | assisted |
| Direktor hisoboti | Intelligence | sheets, crm | autonomous |
| **Shartnoma chop etuvchi** | Back office | runner.office(Word), runner.print | assisted |
| **Skan hujjat kirituvchi** | Back office | runner.scan, files.read_pdf, sheets | assisted |

Ishlab chiqarish uchun qo'shimcha: *Uskuna holat kuzatuvchi* (runner.iot/modbus → telegram, autonomous), *Ombor kamera tekshiruvchi* (runner.camera → operator, assisted), *1C hisob-faktura kirituvchi* (files + onec, assisted). Buxgalteriya firmasi uchun: *Soliq kabinet tekshiruvchi* (runner.browser, led), *Bank ko'chirma yig'uvchi* (runner.browser + sheets, assisted).

Jadvalning 70–80% i sohalar aro takrorlanadi — faqat tool'lar, persona matni va devices.yaml o'zgaradi.

### 8.2 Vertikal pack'lar — 20% qoida bilan (v0.4, SHART)

Prinsip (o'zgarmaydi): yangi mijoz = yangi YAML + persona + tool kalitlari. Kod o'zgarishi ≤ 20% (faqat yangi tool adapteri bo'lsa). Core (orkestrator, ladder, billing, approval, xotira) tegilmaydi.

| Vertikal | Pack agentlari (4-6 ta) | O'zgaradigan 20% (tool/persona) | Qolgan 80% (core'dan) |
|---|---|---|---|
| Chakana do'kon (kiyim, demo) | DM javobchi, buyurtma qabulchi, filial router, FAQ, hot-lead, statistika | `instagram/telegram/sheets`, tovar katalogi, 2 filial | Orkestrator, ladder, billing, approval |
| Marketing agentligi | Brief qabulchi, kontent yozuvchi, reklama hisobotchi, mijozga haftalik xabar, lead kuzatuvchi | `gmail/drive/meta-ads`, GEM rollari (content/ad/research), KPI dashboard | Xuddi shu core + RAG |
| Buxgalteriya outsourcing | Hujjat yig'uvchi, soliq kabinet tekshiruvchi (`runner.browser`, led), bank ko'chirma yig'uvchi, mijoz eslatmachi, oylik hisobot | `runner.browser/scan`, Soliq.uz/bank-kabinet, `sheets`, qat'iy approval | Core + audit log (12 oy) |
| Ishlab chiqarish (zavod) | Uskuna holat kuzatuvchi (`runner.iot/modbus`, autonomous), ombor kamera tekshiruvchi, smena hisoboti, nosozlik eskalatsiyasi, ehtiyot qism buyurtma | `runner.iot/industrial/camera`, Modbus/OPC-UA (v2), `physical` tasdiq | Core + kill switch + screenshot audit |
| Distribyutor (ichki/tashqi) | Buyurtma qabulchi (optom), qoldiq so'rovchi, narx varaqasi yuboruvchi, qarzdorlik eslatmachi, marshrut ro'yxati | `sheets/crm`, prays-list KB, Yandex Maps (mijoz nuqtalari), WhatsApp | Core + MapsWidget |
| Logistika | Buyurtma kuzatuvchi (trek), haydovchi/mijoz xabarchi, ombor kirim-chiqim, kechikish eskalatsiyasi, kunlik reys hisoboti | `webhook (trek API)/telegram/whatsapp`, Maps (marshrut), `sheets` | Core + cron trigger |

Yangi vertikal qo'shish tartibi (3-5 kun): 1) `_template` nusxa → 2) 4-6 agent YAML + 2-4 persona `.md` → 3) `tools.yaml` kalitlari → 4) `devices.yaml` (kerak bo'lsa runner) → 5) 10 ta test → 6) `human_led` ishga tushirish. 6-vertikal to'lgach core'ga tegish taqiqlanadi — faqat pack yoziladi.

### 8.3 To'liq agentlashtirish — "qayerda/qancha" dan "hamma kompyuter ishi" ga (v0.4, SHART)

Maqsad: faqat so'rov-javob emas, inson aralashuvisiz kompyuterda qilinadigan barcha ish — agentda. Prinsip: har xodim roli = 1 pack ichida 3-7 agent. Odam faqat tasdiqlaydi va istisnoni hal qiladi.

Ishlar 3 sinfga bo'linadi (qay tartibda olinadi):
- **Sinf A (API/baza, 1-2 kun/pack):** lead javob, buyurtma yozish, qoldiq tekshirish, hisobot yig'ish, eslatma yuborish, hujjat shablon to'ldirish. Ishonchlilik 99%. Birinchi olinadi.
- **Sinf B (fayl/ofis/brauzer, 3-7 kun):** Excel yig'ma, Word shartnoma, PDF skan → baza, Soliq.uz/bank kabinet, pochta saralash. `runner.office/browser/scan` orqali. Ikkinchi olinadi.
- **Sinf C (eski dastur/ekran, 2-4 hafta):** 1C COM, antikware GUI, printer/skaner jismoniy. Faqat `runner.screen` fallback, har qadam screenshot + tasdiq. Oxirgi olinadi.

Har rol uchun takeover tartibi (ladder bilan): 1-2 hafta `human_led` (agent qoralama, odam bosadi) → xato ≤5% bo'lsa `assisted` (agent qiladi, pul/chop etish/o'chirishda tasdiq) → 30 vazifa toza bo'lsa `autonomous` (faqat log). `destructive/physical` — doim tasdiq, istisnosiz.

Misollar (rol → agentlar): sotuvchi (DM javobchi, buyurtma yozuvchi, qoldiq aytuvchi), buxgalter (ko'chirma yig'uvchi, kabinet tekshiruvchi, eslatmachi, hisobotchi), omborchi (kirim-chiqim yozuvchi, kamera tekshiruvchi, qoldiq ogohlantiruvchi), marketing (brief oluvchi, kontent yozuvchi, hisobotchi), direktor (kunlik/haftalik hisobot, eskalatsiya qabulchi). Odam qoladigan ish: muzokara, imzo, jismoniy tekshirish, nizo hal qilish.

---

## 9. Ochiq savollar

- Billing: setup + oylik (token ichida) — tavsiya; ekran vazifalari alohida paketmi?
- Screenshot audit qayerda saqlanadi — default cloud yoki mijoz? (tavsiya: mijoz tanlaydi, default cloud shifrlangan)
- Pack'lar keyinchalik marketplace?
- O'zbekiston hostingi yuridik talabi.
- Runner Linux qo'llab-quvvatlash v1'da kerakmi? (tavsiya: Windows + macOS v1, Linux/RPi v2)

---

## 10. Tashqi repo tahlili — nima olamiz, nima olmaymiz (v0.3)

| Repo | Nima | Star/fork | Litsenziya | Hukm |
|---|---|---|---|---|
| `msitarzewski/agency-agents` | 100+ prompt-persona markdown (engineering, sales, marketing...) + `install.sh --tool claude-code` | 152k / 24.5k | MIT | **Prompt kutubxona sifatida ol.** Runtime yo'q (xotira, tool, billing, multi-tenant yo'q). `packs/_template/prompts/` ga 5-10 ta personani tarjima qilib sol, to'g'ridan fork qilmа. Fable/Astra "ustiga qur" degani xato — bu prod asosi emas. |
| `Moh4696/build-ai-agents-free` | Beginner tutorial: `langchain create_agent + Groq llama-3.3 + Gemini fallback + DuckDuckGo + InMemorySaver` | 334 / 103 | MIT | **Faqat o'rganish uchun.** `InMemorySaver` RAM'da (restartda o'chadi), auth/tenant/uzbek/billing yo'q, free-tier prompt'da train qiladi (maxfiylik riski). Prod'da `InMemorySaver → Postgres checkpointer`, `Groq → Claude Haiku` ga almashtiriladi. Astra'ning "bepul stack'da prod qil" g'oyasi noto'g'ri. |
| `RubenM1990/APEX-UI` | Orb + reasoning-graph UI only (Next.js 15, `ApexOrb`, `ReasoningWeb`, shader) | 57 / 31 | MIT (brend yo'q) | **Faqat vizual referens.** Backend/ovoz/humanoid yo'q. `ROSTER → pack.yaml`, shader tashlanadi. 1:1 klon qilinmaydi. |
| `tinyhumansai/openhuman` | Local-first harness: Memory Tree + Obsidian, tinyagents/tinyflows, 100+ OAuth, 17 kanal, Whisper | 39.7k / 3.9k | **GPL-3.0** | **Fork QILMA.** GPL tarqatilgan binary'da source ochishni talab qiladi — yopiq SaaS o'ladi. Faqat MCP/webhook chegarasi orqali integratsiya (runner host sifatida). Pattern o'g'irla: approval-gated workflow, TokenJuice, auto-fetch. |

**Fable/Astra fikrlari haqida yakun:**
- To'g'ri aytgani: bitta mijoz + bitta kanal + bitta muammo (Turkish Baby Telegram), text-first, ovoz keyin, n8n/Flowise demo uchun.
- Xato aytgani: (1) NestJS + Qdrant + k8s ni MVP'ga tiqish — yakka odamga og'ir, FastAPI + Supabase + pgvector yetadi; (2) bepul Groq/Gemini'da mijoz ma'lumotini yuritish — maxfiylik buziladi; (3) `agency-agents`/`build-ai-agents-free` ustiga prod qurish — ular prompt to'plam/tutorial, billing/tenant/runner yo'q.
- Sening qo'shimchalaring (Maps, kundalik avto-vazifa, almashinadigan UI) to'g'ri, lekin Bosqich 5+ da widget sifatida, MVP'da emas.

---

## 11. JARVIS PDF tahlili + BYOS siyosati (v0.4)

Manba: `JARVIS AI TIZIMI — 0 dan 100 gacha.pdf` (@kozimovich_, 13 sahifa). Shaxsiy operatsion tizim: ChatGPT (miya) + Claude Code (qo'l) + Gemini Gems (yordamchi rollar) + NotebookLM (manbaga tayangan tadqiqotchi) + Obsidian (xotira). Prinsip: `siz → topshiriq → tahlil → xotira → ijro → natija → SOP`.

### 11.1 PDF'dan olinadigan 7 xulosa (bizga mos)

1. **Rol ajratish.** Bitta AI'ga hamma ishni yuklama. Bizda: `sales/support/content/runner` agentlari + `tool_policy` (§3.4).
2. **JARVIS BRIEF → agent input standarti.** `MAQSAD/KONTEKST/MANBALAR/CHEKLOVLAR/NATIJA FORMATI/MUDDAT` — orkestrator kirish schemasi shu bo'ladi. Ma'lumot yetmasa agent ishlamaydi, avval savol beradi.
3. **Obsidian struktura → `packs/<client>/knowledge/` mapping.** `00 INBOX … 10 ARCHIVE`, har loyiha faylida `maqsad/auditoriya/offer/KPI/materiallar/qarorlar`. Bizning `dialect.yaml`, `SOP`, `DECISIONS` shu nomlar bilan yuradi; Obsidian vault bo'lsa — aynan shu papkalarni o'qiymiz (import, qayta yozish yo'q).
4. **NotebookLM qoidasi → RAG qoidasi.** Faqat manbaga tayan, topilmasa "manbada topilmadi" de, har xulosaga manba ko'rsat, fakt/taxminni ajrat. `kb` tool'da majburiy.
5. **CLAUDE.md → runner xavfsizligi.** "Avval o'qi, keyin o'zgartir; destructive'dan oldin reja + backup + ruxsat; kichik qaytariladigan qadam; UTF-8 o'zbekchani buzma" — bular `devices.yaml` + approval (§3.5, F5) ga ko'chiriladi. Birinchi runner vazifa har doim read-only audit.
6. **Gem rollari → pack personalari.** `CONTENT EDITOR / AD CREATIVE / RESEARCHER / SCRIPT DOCTOR` — Turkish Baby Pack 0 dagi kontent/reklama agentlarining boshlang'ich prompti shu.
7. **"Birdaniga qurma" qoidasi.** Bitta real muammo ("yangi mijozni 30 daqiqada o'rganish" / "kuniga 50 savolga javob") mukammallashgach keyingisi. Bosqichlar (§6) shunga mos.

### 11.2 PDF'dan olinmaydigan narsalar

Qo'lda copy-paste workflow, ChatGPT Project + chatlarda qolgan bilim, Telegram/Instagram 24/7 avtomatika yo'qligi, billing/tenant/approval navbati yo'qligi, o'zbek STT/TTS va Maps yo'qligi. PDF — yakka odam unumdorligi, bizning Pack 0 — mijozlarga 24/7 sotuv. Turli ish.

### 11.3 BYOS (user o'z Claude Code / Codex / Gemini obunasi bilan) — hukm

Savol: user o'z MacBook'ida Claude Code / Codex / Antigravity (Gemini) o'rnatib, o'z subscription'i bilan bizning ishlarni yursa bo'ladimi (Hermes'ga o'xshash router orqali)?

**Qisqa javob: shaxsiy Jarvis uchun HA, biznes sotuv agenti uchun YO'Q.**

| Holat | BYOS (o'z obunasi + local CLI) | API (bizning cloud, Haiku) |
|---|---|---|
| Direktor o'ziga kontent/tahlil/hisobot (1 odam) | ✅ Ruxsat. Arzon ($20 sub), PDF'dagi aynan shu use-case | Shart emas |
| Turkish Baby Telegram/Instagram 24/7 sotuv (yuzlab mijoz) | ❌ Taqiq. Sabablar pastda | ✅ Majburiy (SLA, limit, billing) |

Nega biznesga BYOS bo'lmaydi:
1. **ToS/ban riski.** Claude Pro/Max va ChatGPT Plus — yakka foydalanuvchi, qayta sotish va uchinchi shaxslarga backend sifatida ulash taqiqlangan. Mijozlarning xabarlarini direktorning shaxsiy logini orqali haydash = account sharing + resale → ban, ogohlantirishsiz. Gujarat emas, real.
2. **Ishonchsizlik.** Haftalik/soatlik limitlar pik vaqtda tugaydi (reklama oqimi paytida buyurtma yo'qoladi), OAuth token tunda expire bo'ladi, CLI yangilanishi avtomatikani sindiradi. 90% test (§F8) dan o'tmaydi.
3. **Billing yo'q.** Tokenni tenant bo'yicha sanab bo'lmaydi, limit qo'yib bo'lmaydi, mijozga xarajat isbotlanmaydi.
4. **Xavfsizlik.** Shaxsiy token Mac mini'da, xodimlar kira oladigan joyda — leak = butun akkaunt ketadi.

**Arxitektura qarori — 2 trek:**
- **Trek B (Biznes, API):** Telegram/IG sotuv, buyurtma, to'lov eslatma — faqat cloud Haiku API, JWT+billing (§8 tamoyil). Turkish Baby shu.
- **Trek P (Shaxsiy, BYOS):** direktor MacBook'ida local adapter (`runner.byos`: `claude_cli` / `codex_cli` / `gemini_cli` — `claude -p --allowedTools`, `codex exec`, `gemini -p`). Faqat o'z vault'i, o'z obunasi, hech qachon mijoz trafigi u orqali o'tmaydi. Log'ga faqat metadata (vaqt, model, holat) — prompt matni emas.

Hermes g'oyasi shu yerda ishlaydi: qaysi CLI borligini aniqlab (claude → codex → gemini fallback), shaxsiy vazifani mos adapterga beradi. Lekin Hermes'ni biznes treki bilan aralashtirish taqiqlanadi — kodda alohida `byos.*` tool guruhi, `approval.required_for: [byos.*]` emas, `deny_always` biznes trassasi uchun.

### 11.4 Yakuniy qaror — raqam bilan (v0.4)

Hermes modeli = har odam o'z PC, o'z obunasi. Bizning ish = ko'p mijoz → bitta biznesga 24/7. Yo'nalish teskari. Shuning uchun:

| Savol | Hermes nusxasi (har kim o'z sub'i) | Bizning yo'l (biz beramiz, API) |
|---|---|---|
| 100 mijoz birdan yozsa nima bo'ladi? | Direktorning Pro limiti (45 msg/5 soat) 10 daqiqada tugaydi, qolgan 90 mijoz javobsiz | Haiku autoscale, hammasiga javob (oyiga ~$15-30 token) |
| Kim to'laydi? | 5 xodim × $20 = $100/oy, baribir ban riski bor | Mijoz bizga $150-300/oy (ichida $15-30 token + bizning foyda) |
| Kim o'chira oladi? | Xodim obunani to'xtatsa butun tizim o'ladi | To'lamasa biz JWT'ni o'chiramiz, 30 sek'da to'xtaydi |
| 1 dialog narxi | Chalkash (limit ichida "tekin"dek, aslida $20/oy + ish yo'qolishi) | Aniq: ~10 xabar ≈ 5k token ≈ $0.005-0.01 |

**Qaror (o'zgarmaydi):**
1. Hozir faqat Trek B quriladi — Turkish Baby Telegram/IG sotuv, kalitlar bizda, mijozdan obuna talab qilinmaydi.
2. Trek P (shaxsiy Jarvis, o'z obunasi) — faqat direktor so'rasa, keyin, bonus sifatida. MVP'ga kirmaydi.
3. Hermes/openhuman fork yo'q. Faqat `runner.byos` adapter interfeysi qoldiriladi (bo'sh, v2 uchun).

### 11.5 Token/kattalik qo'rquvi + call-center real narxi (v0.4)

Savol to'g'ri: CRM/ERP/DB/mijoz yozishmalari input'ni shishirib yuboradimi? Ha — agar butun bazani prompt'ga tiqsang. Biz tiqmaymiz. Qoida: **DB prompt'ga kirmaydi, tool orqali filtrlanib kiradi.**

- Katalog 5000 tovar bo'lsa ham agentga top-5 qaytadi (`products.search(query, limit=5)` — har biri 100 token). 5000 × 100 emas, 5 × 100 = 500 token.
- Tarix butunligicha emas — oxirgi 6 xabar + xulosa (summary 200 token).
- System prompt (~800 token) Claude prompt-cache'da — 90% arzon.
- Har tool output limiti: max 2000 token, ortig'i kesiladi + `TokenJuice`压缩 (openhuman patterni).
- RAG: chunk top-3, har biri 400 token. Butun PDF emas.
- Per-tenant oylik limit + 80% ogohlantirish (§4) — mijoz "cheksiz" gapira olmaydi.

Real hisob (Haiku 3.5: $0.80/1M in, $4/1M out, cache bilan):
- Text-MVP, 3000 suhbat × 10 turn: input ~15-25M (cache'dan keyin) ≈ $12-20, output ~5-9M ≈ $20-36. Jami **$20-70/oy**. Oldingi "$25" — pastki chegara edi, uzr — to'g'risi shu oraliq.
- RAG + CRM qo'shilsa: +$10-20 (embedding arzon, pgvector local).
- Shishib ketsa (tarix kesilmasa, cache'siz): $150+ bo'ladi — shuning uchun limitlar kodda majburiy, mijoz xohishiga qo'yilmaydi.

Call-center (STT→LLM→TTS) 1 daqiqa tannarxi:
- STT Groq Whisper: ~$0.002 | LLM (3-4 turn): ~$0.01-0.02 | TTS ElevenLabs: ~$0.03-0.05 | Telefoniya (O'Z VoIP/MTT): ~$0.02-0.05. Jami **~$0.05-0.12/daq**.
- 1000 qo'ng'iroq × 3 daq = **$150-360/oy + alohida raqam/operator shartnomasi**. Text'dan 5-10x qimmat, sifat riski yuqori (shovqin, sheva, uzilish).
- Qaror: ovoz — v2, faqat ish vaqtida, faqat 1 raqamda pilot, yozuv + inson eskalatsiyasi bilan. Birinchi pul text'dan keladi.

Turkish Baby to'liq JD rejasi (audit → prod): (1) audit 2-3 kun (top-5 savol, buyurtma oqimi, CRM/Excel, filiallar), (2) text-MVP 1.5 hafta, (3) Sheets/CRM ulash 1 hafta, (4) WhatsApp + RAG (hujjatlar) 2 hafta, (5) voice pilot 3 hafta (alohida narx), (6) monitoring/eval/SOP + ladder ko'tarish. Har faza alohida qabul qilinadi, keyingi faza oldingisi pul to'lagach boshlanadi.

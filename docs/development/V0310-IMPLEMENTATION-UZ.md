# v0.3.10 implementation: re-engagement loop

## Scope

`BACKLOG.json`dagi navbatdagi blok — `crm_reengagement_loop` — bajarildi. Bu CRM ekotizimi
hujjatidagi §4.1 siklini (javobsiz qolgan lid → qayta yozish → operator eskalatsiyasi)
haqiqatan ishga tushiradi: `crm.lead.stalled` feed'i endi **rejalashtirilgan, dedup qilingan
va approval-gated** oqimga ulanadi.

Production qarori o‘zgarmaydi: **NO_GO**. Live provider va live model acceptance yo‘q.

## Arxitektura

`platform_runtime/reengagement.py` — `ReengagementLoop(engine, agent_loop)`.

Bir sikl (`tick`) bosqichlari:

1. **Ledger sync.** Tugagan run'lar holati ledger'ga ko‘chiriladi (`succeeded` → `settled`).
2. **Due policy.** `enabled=1 AND next_due<=now` bo‘lgan bitta policy olinadi (bir tick — bir
   sikl, `engine.tick` bilan bir xil falsafa).
3. **Authority qayta tekshiruvi.** Har siklda `require_active` + owner authority. Owner
   bekor qilinsa policy **disable** qilinadi va audit yoziladi.
4. **Feed o‘qish.** Feed **oddiy tool handler orqali** o‘qiladi:
   `registry.get('crm.lead.stalled').handler(...)`. Ya'ni agent tool ruxsati, connection
   allowlist va schema gate xuddi qo‘lda chaqiruvdagidek ishlaydi. Bu ataylab: koordinator
   uchun alohida, zaifroq yo‘l yo‘q.
5. **Har lid uchun claim + run.** Ledger'da compare-and-set bilan urinish olinadi, so‘ng
   `AgentLoop.create` bilan oddiy run ochiladi.
6. **Yakun.** `next_due = now + interval_seconds`, `last_run`, audit yozuvi.

## Uch muhim qaror

### 1. Provider xatosi "lid yo‘q" degani emas

Feed `Conflict` bilan yiqilsa, sikl **qayta rejalashtiriladi va audit qilinadi**; ledger'ga
hech narsa yozilmaydi, run ochilmaydi, policy o‘chirilmaydi. Agar xato bo‘sh ro‘yxatga
aylansa, follow-up sikli **jimgina o‘lib qolardi** — buni test aynan tekshiradi
(`test_provider_failure_is_not_read_as_no_leads`).

Boshqa tomondan, feed **ruxsat rad etishi** (`Forbidden`) policy'ni o‘chiradi: aks holda
buzilgan konfiguratsiya har siklda behuda aylanardi.

### 2. Dedup — ledger, va faqat qayta urinish mumkin bo‘lgan holat

**Ledger kaliti `(tenant, connection, lead_id)` — policy kirmaydi.** Connection bu
mijozga qaragan manzil; bir CRM‘ga ikki policy qarasa, ikkalasi ham bitta lidga yozishi
mumkin bo‘lardi. Shuning uchun lid **connection darajasida** bir marta claim qilinadi,
`policy` ustuni esa faqat iz qoldirish uchun yoziladi. Attempt hisoblagichi ham umumiy,
ya’ni `max_attempts` qancha policy bo‘lishidan qat’i nazar mijozga tegishlar sonini
cheklaydi.

| Ledger holati | Ma'nosi | Avtomatik qayta urinish |
|---|---|---|
| `queued` | run ochilgan | Ha — cooldown + `max_attempts` ichida |
| `settled` | run muvaffaqiyatli | **Yo‘q** — mijoz allaqachon xabar oldi |
| `uncertain` | provayder yozuvi noaniq | **Yo‘q** — ko‘r-ko‘rona retry ikki marta xabar yuborardi |
| `escalated`, `cancelled`, `needs_input` | qaror odamga tegishli | **Yo‘q** |
| `exhausted` | urinish limiti tugadi | Yo‘q |
| `failed` | run ochilmadi (provider emas) | Cooldown keyin |

Claim `last_attempt` ustida compare-and-set bilan **bitta transaction ichida** qilinadi, ya'ni
ikki worker bir lidni ikki marta boshlamaydi. Qo‘shimcha himoya: `request_key =
reeng:{policy}:{lead_id}:{attempt}` — bir urinish ikki marta ochilmaydi.

### 3. Outreach baribir approval-gated

Loop hech narsa yozmaydi. Yozuv `crm.timeline.attach_message` orqali bo‘ladi, u
`risk='write'` → `approval_needed=1` → `p_approvals`da `pending`. Test buni run ichidagi
haqiqiy step'dan o‘qiydi (`test_outreach_step_still_requires_approval`). Ya'ni loop mijozga
tasdiqsiz yozadigan yangi yo‘l **yaratmaydi**.

## Boshqaruv yuzasi

**HTTP** (`app/platform_api.py`, owner-scoped):

| Endpoint | Rol |
|---|---|
| `GET /platform/{tenant}/reengagement` | owner, operator |
| `PUT /platform/{tenant}/reengagement/{policy}` | **owner** |
| `GET /platform/{tenant}/reengagement/{policy}/ledger` | owner, operator |
| `POST /platform/{tenant}/reengagement/{policy}/sync` | **owner** |

`ReengagementPolicy` modeli `extra='forbid', strict=True`: noma'lum maydon, noto‘g‘ri tur va
chegaradan tashqari qiymat 422 beradi.

**Worker** (`app/worker.py`) har tenant uchun `reengagement.tick(tenant)` ni `run_schedules`
bilan bir tsiklda chaqiradi.

## Sozlamalar va ularning chegaralari

| Sozlama | Default | Chegara | Nega chegara |
|---|---:|---|---|
| `inactive_minutes` | 120 | 1..20160 | 2 haftadan eski lidni "javobsiz" deb hisoblash ma'nosiz |
| `cooldown_seconds` | 86400 | **300**..2592000 | 5 daqiqadan tez — spam |
| `max_attempts` | 2 | 1..10 | Bitta mijozga avtomatik urinishlar soni |
| `max_per_cycle` | 5 | 1..20 | Bitta sikl narxi (har lid — bitta LLM run) |
| `interval_seconds` | 3600 | 300..604800 | Sikl tezligi |
| `max_steps` | 4 | 1..12 | Run qadam byudjeti |
| `max_seconds` | 1800 | 60..86400 | Run vaqt byudjeti |

## Testlar topgan haqiqiy nuqsonlar

Bu blokda testlar uch marta **haqiqiy** xatoni ushladi:

| Topilma | Nega muhim |
|---|---|
| Reconciler’dagi eski `if driver ==` shoxobchasi qolib ketgan | `onec`/`custom_webhook` **yozish mumkin, lekin reconcile qilib bo‘lmaydigan** bo‘lardi |
| Dedup kaliti `(tenant, policy, connection, lead_id)` edi | Bir CRM’ga ikki policy qarasa, **bitta mijozga ikki marta** yozilishi mumkin edi. Kalit `(tenant, connection, lead_id)`ga o‘zgartirildi |
| `p_migrations` 7-versiyasi ikkita testda e’lon qilinmagan | `test_v036_upgrade` va `test_v037_migration` yiqildi — bu migratsiya intizomi ishlayotganini ko‘rsatadi |

| Guruh | Test |
|---|---:|
| `runtime_tests/test_reengagement.py` | 29 |
| `integration_tests/test_reengagement_http.py` | 7 |
| Jami yangi | **36** |

`python -m unittest discover -s runtime_tests`:

| | Oldin | Keyin |
|---|---:|---:|
| Topilgan test | 997 | **1026** |
| Error | 150 | **150** |
| Failure | 1 | **1** |

`python -m pytest integration_tests`: **4 failed, 80 passed, 11 errors** (oldin 5 failed, 72
passed, 11 errors). 15 muammoning hammasi `cryptography` yo‘qligi; 1 tasi (CORS/route testi)
oldingi blokda tuzatilgan. Yangi regressiya yo‘q.

## Chegaralar (yashirilmaydi)

1. **React UI yo‘q.** Owner HTTP API orqali sozlaydi; dashboard ekrani keyingi blok.
2. **Live model kerak.** Run'lar `llm.agent_loop_enabled=true` va `llm.model` bo‘lmasa
   `escalated` bo‘ladi — bu `ResultPlanner`ning aniq opt-in talabi. Ya'ni loop ishlaydi, lekin
   xabar matnini real provayder yozadi.
3. **Live CRM acceptance yo‘q.** Feed fake transport bilan sinaldi.
4. **Eskalatsiya siyosati yo‘q.** Hozir har lid `max_attempts` marta uriniladi; "hot lead →
   menejerga Telegram alert" qismi hali yo‘q.
5. **Ledger tozalash yo‘q.** Ledger cheksiz o‘sadi; retention siyosati keyingi blok.
6. **Bitta sikl — bitta policy.** Ko‘p policy bo‘lsa, bir worker tick'ida faqat bittasi
   ishlanadi (bounded work), lekin ketma-ket tick'lar qolganini oladi.

## Keyingi qadam

`reengagement_ui_and_live_acceptance`: dashboard ekrani (policy forma + ledger jadvali),
hot-lead eskalatsiya, ledger retention, va haqiqiy 1C/custom endpoint + model provider bilan
staging acceptance.
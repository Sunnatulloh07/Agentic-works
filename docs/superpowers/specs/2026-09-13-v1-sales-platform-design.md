# Agent Platform - To'liq mahsulot dizayni

**Sana:** 2026-09-13  
**Holat:** Qurishdan oldingi asosiy dizayn  
**Maqsad:** Butun Agent Platform'ni yagona tizim sifatida loyihalash, barcha asosiy modullarni qurish va faqat to'liq tizim tayyor bo'lgandan keyin umumiy acceptance test o'tkazish.

## 1. Mahsulot mohiyati

Agent Platform - umumiy core, mijozga xos pack va kerak bo'lsa mijoz kompyuterida ishlaydigan Local Runner'dan iborat ko'p-tenant platforma. Mijoz o'z biznesidagi AI xodimlar xaritasini ko'radi. Har bir agent aniq vazifa, tool, ruxsat va avtonomiya darajasiga ega bo'ladi.

Asosiy qoida: **core kod mijozga bog'lanmaydi; mijoz farqi pack konfiguratsiyasida bo'ladi.**

## 2. Qurish modeli

Biz platformani kichik MVP sifatida chiqarib yubormaymiz. Avval to'liq talablar, arxitektura, ma'lumot modeli, barcha asosiy modullar, tashqi integratsiyalar, UI, runner, security va deployment quriladi. Keyin ular yagona tizimga ulanadi, shundan so'ng umumiy test, xatolarni tuzatish, regression, staging va final pilot o'tkaziladi.

Ichki qurish vaqtida syntax, import va typecheck nazorati ishlatiladi. Bu oraliq release emas. Haqiqiy funksional va acceptance baholash barcha tizim ulanganidan keyin o'tkaziladi.

## 3. To'liq funksional qamrov

### Core va orchestrator

- Xabar, ovoz, webhook, cron yoki qurilma hodisasini qabul qilish.
- Intent aniqlash va agentga yo'naltirish.
- Bir necha qadamli reja yaratish.
- Tool ustuvorligi: API, CLI/COM, brauzer, ekran.
- Har qadamni trace qilish.
- Timeout, retry, cancellation va xato eskalatsiyasi.
- Natijani tekshirish va hisobot berish.

### Agent modeli

Agent pack orqali ta'riflanadi: id, nom, bo'lim, persona, tools, tool policy, ladder, approval, memory, trigger, testlar va til uslubi. Tool'siz agent faqat chat bo'lib qolmaydi; har agent kamida bitta real vazifaga va tool'ga bog'lanadi.

### Tool va integratsiyalar

Cloud: Telegram, Instagram/Meta, Gmail, Google Sheets/Drive/Calendar, CRM, HTTP/webhook, PDF/DOCX/XLSX.

Runner: fayl, Excel/Word, printer/skaner, browser, Windows dasturlari, 1C, ekran, kamera, IoT va sanoat protokollari.

Har tool uchun schema, read/write/destructive/physical daraja, rate limit, tenant allowlist va approval policy bo'ladi.

### Ladder va approval

- `human_led`: agent tavsiya yoki qoralama beradi.
- `human_assisted`: agent bajarishga tayyorlaydi, xavfli qadam oldidan operator tasdiqlaydi.
- `autonomous`: ruxsat berilgan ishni mustaqil bajaradi, audit qoladi.

Yuborish, o'chirish, to'lov, chop etish, ekran va fizik qurilma harakatlari default bo'yicha tasdiq talab qiladi. Destructive va physical tool'lar Owner alohida tasdiqlamasdan autonomous bo'lmaydi.

### Xotira va bilim

Tenant knowledge base, hujjat chunk/embedding, agent memory scope, suhbat xotirasi, biznes faktlari va TTL'li qurilma holati bo'ladi. Tenant yoki agent boshqasining memory collection'ini ko'ra olmaydi.

### Kanallar

Telegram, Instagram DM, Web UI, voice, webhook, cron va runner hodisalari. Channel faqat transport/parsing qiladi; biznes qarori application service va orchestrator'da qoladi.

### Local Runner

Runner cloud core bilan outbound WebSocket orqali ulanadi. Unda LLM kaliti yoki biznes miyasi bo'lmaydi, faqat server tasdiqlagan tool'ni allowlist doirasida bajaradi. Majburiy qismlar: 24 soatlik JWT, heartbeat, device identity, task claim/lease, reconnect, kill switch, allowlist, deny_always, screenshot audit, signed update, rollback va offline freeze.

Bir task ikkita runner tomonidan bajarilmasligi uchun atomic claim va lease ishlatiladi.

## 4. Arxitektura qatlamlari

```text
Telegram / Instagram / Web / Voice / Cron / Runner events
                         |
                    API Gateway
                         |
            Auth + Tenant + Rate Limit
                         |
             Application Services
                         |
       Orchestrator + Agent Runtime + Policy
                         |
                 Tool Router / MCP
              /            |             \
       Cloud tools     Approval       Runner tools
                         |
             Durable state + Memory + KB
                         |
              Audit + Trace + Metrics
```

- Gateway request, signature, auth, tenant va correlation ID uchun.
- Application service biznes use-case va state transition uchun.
- Orchestrator intent, reja, agent va tool tanlash uchun.
- Policy ladder, approval va xavf darajasi uchun.
- Storage transaction, idempotency, state va migration uchun.
- Runner faqat lokal ijro uchun.
- UI boshqaruv va kuzatuv uchun.

## 5. Ma'lumot modeli

Tenant, user/role, pack version, agent, tool, conversation/message, task/run/step, approval, order yoki biznes hujjati, delivery attempt, memory/document/chunk, runner/device, audit event, usage/billing counter va notification entity'lari bo'ladi.

Har tenant-scoped jadvalda `tenant_id` bo'ladi. Har external write stable idempotency key bilan bajariladi. Approval tasdig'i external write bilan aralashtirilmaydi: avval durable state, keyin retry qilinadigan delivery.

## 6. Xavfsizlik

- Production fail-closed.
- Secret faqat environment yoki vault'da.
- JWT tenant-bound.
- Owner, operator, integrator va super-admin role'lari.
- Webhook signature majburiy.
- Tool allowlist va deny_always.
- Password, karta, CVV va maxfiy maydonlarga runner yozmaydi.
- Sensitive log va screenshot maskalanadi.
- Approval va tool chaqiruvi audit qilinadi.
- Tenantlararo read/write test qilinadi.
- Runner token rotation va offline freeze ishlaydi.

## 7. UI

Agent map, agent detail, ladder, task timeline, approval queue, order/business record, delivery status, retry, runner/device holati, audit viewer, hisobot, tenant settings, role va til sozlamalari bo'ladi.

## 8. Umumiy qabul mezonlari

Tizim quyidagilar bajarilmaguncha tayyor deb e'lon qilinmaydi:

1. Telegram va Instagram real xabarni qabul qiladi va javob qaytaradi.
2. LLM structured intent va tool-calling bilan ishlaydi.
3. Approval'siz xavfli action o'tmaydi.
4. Bitta external event ikki marta bajarilmaydi.
5. Sheets/CRM/runner failure'da ma'lumot yo'qolmaydi.
6. Tenantlar to'liq ajratilgan.
7. Runner task lease duplicate execution'ni to'sadi.
8. UI barcha asosiy holatlarni ko'rsatadi.
9. Backup, restore, migration va rollback tekshirilgan.
10. Security, load, failure, recovery va browser testlari o'tgan.
11. Real staging'da uch kunlik pilotda task yo'qolmagan.
12. Hujjatdagi har bir “tayyor” da'vo dalilga ega.

## 9. Hozirgi kodning o'rni

FastAPI, pack, approval, SQLite, operator UI va runner kodlari boshlang'ich baseline sifatida saqlanadi. Ular final platforma emas. Yetishmayotgan qismlar: real tashqi adapterlar, LLM, memory/RAG, to'liq runner protocol, production auth, migration, billing, monitoring va umumiy end-to-end test.

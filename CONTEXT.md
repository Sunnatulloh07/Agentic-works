# Joriy holat: v0.3.7 development, IN_PROGRESS

Joriy dalil va qolgan ishlar `docs/development/PROGRESS-UZ.md`, `BACKLOG.json`, `V037-IMPLEMENTATION-UZ.md` da. 821 lokal test PASS, production NO_GO. OAuth/Google/sync bloklari qo‘shildi. Quyidagi matn tarixiy checkpoint, joriy status emas.

> **Joriy development: v0.3.6, oraliq checkpoint.** Yangi source, audit va 709 ta lokal test dalili: [development reyestri](docs/development/PROGRESS-UZ.md) va [implementatsiya hisoboti](docs/development/V036-IMPLEMENTATION-UZ.md). To‘liq mahsulot emas. Quyidagi oldingi bo‘limlar v0.3.5 va avvalgi holat kontekstini saqlaydi.

# Agent Platform - Domen lug'ati

Bu fayl biznes va arxitektura terminlarining umumiy ma'nosini saqlaydi. Implementation tafsilotlari bu yerga yozilmaydi.

## Tenant

Platformadagi bitta mijoz biznesi. Tenant ma'lumoti, agentlari, pack'i, task'lari, xotirasi va tashqi integratsiyalari boshqa tenantlardan ajratiladi.

## Agent

Tenant nomidan aniq biznes vazifasini bajaradigan AI xodim. Agent persona, vakolat, tool va ladder darajasiga ega.

## Tool

Agent ishlatadigan tashqi qobiliyat. Tool faqat nom emas: u input/output, xavf darajasi, ruxsat, limit va audit ma'nosiga ega.

## Task

Agent bajarishi kerak bo'lgan biznes vazifa. Task bir yoki bir nechta qadamdan iborat bo'lishi mumkin.

## Run

Bitta task'ning amaliy bajarilish jarayoni. Run reja, qadamlar, tool chaqiruvlari, natijalar va xatolarni o'z ichiga oladi.

## Approval

Xavfli yoki biznes jihatdan muhim action oldidan vakolatli odamning explicit qarori. Approval task yoki tool bajarilishidan oldin policy orqali talab qilinadi.

## Delivery

Tasdiqlangan natijani tashqi tizimga yozish yoki yuborish jarayoni. Approval muvaffaqiyati delivery muvaffaqiyatini anglatmaydi.

## Ladder

Agentning avtonomiya darajasi: human-led, human-assisted yoki autonomous.

## Pack

Tenant'ning konfiguratsion ta'rifi: agentlar, persona, tool policy, til, filial, mahsulot, memory va triggerlar.

## Runner

Mijoz qurilmasida ishlaydigan, cloud core bergan ruxsatli tool'larni bajaradigan lokal executor. Runner biznes qarorini yoki LLM reasoning'ni o'zi qilmaydi.

## Tool risk

Tool ta'sirining xavf klassi: read, write, destructive yoki physical. Destructive va physical action default bo'yicha approval talab qiladi.

## Idempotency

Bir xil tashqi event yoki action takror kelganda natija ikkinchi marta yaratilmasligi kafolati.

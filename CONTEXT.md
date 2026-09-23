# Joriy holat: v0.5 development, IN_PROGRESS, production NO_GO

Joriy dalil va qolgan ishlar `docs/development/PROGRESS-UZ.md`, `docs/development/QOLGAN-ISHLAR-INVENTAR-UZ.md` va `docs/IMPLEMENTATION-STATUS.md` da. Status raqamlari faqat `README.md` yuqorisidagi blokda saqlanadi, bu yerda takrorlanmaydi.

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

Tenant'ning konfiguratsion ta'rifi: agentlar, persona, tool policy, til, filial, mahsulot, memory va triggerlar. Pack'da e'lon qilingan har bir maydon hali kod tomonidan o'qilmaydi — quyidagi «Pack maydonlari» jadvaliga qarang.

## Runner

Mijoz qurilmasida ishlaydigan, cloud core bergan ruxsatli tool'larni bajaradigan lokal executor. Runner biznes qarorini yoki LLM reasoning'ni o'zi qilmaydi.

## Tool risk

Tool ta'sirining xavf klassi: read, write, destructive yoki physical. Destructive va physical action default bo'yicha approval talab qiladi.

## Idempotency

Bir xil tashqi event yoki action takror kelganda natija ikkinchi marta yaratilmasligi kafolati.

---

## Pack maydonlari: e'lon qilingan va iste'mol qilinadigan

`pack.yaml` schema'si (`api-python/app/packs.py`) ba'zi maydonlarni qabul qiladi, lekin
runtime ularni **hali o'qimaydi**. Pack muallifi adashmasligi uchun holat aniq yozilgan.
«E'lon qilingan, iste'mol qilinmaydi» degani: YAML validatsiyadan o'tadi, qiymat
saqlanadi, ammo hech bir xulq shunga qarab o'zgarmaydi.

| Maydon | Holat | Izoh |
|---|---|---|
| `persona: prompts/x.md` | **iste'mol qilinadi** (2026-09-15 dan) | Yuklash paytida fayl o'qiladi va agentning system prompt'iga (`agent.prompt`) yoziladi. Yo'l pack chegarasidan chiqolmaydi va hajmi cheklangan. |
| `tools` | **iste'mol qilinadi** | `engine._validated` har bir qadamni shu ro'yxatga solishtiradi. |
| `ladder` | **iste'mol qilinadi** | `human_led` / `human_assisted` / `autonomous` — approval qarorini belgilaydi. |
| `approval` | **iste'mol qilinadi** | Nomi bo'yicha majburiy tasdiq talab qiladigan tool'lar. |
| `allowed_recipients` | **iste'mol qilinadi** | Outbound qabul qiluvchi oq ro'yxati; `autonomous` uchun per-message tasdiqdan ozod qiladi. |
| `allowed_connections` | **iste'mol qilinadi** | `connectors.read`, `database.*` uchun majburiy. |
| `triggers` | **e'lon qilingan, iste'mol qilinmaydi** | Hech bir scheduler yoki router uni o'qimaydi. Jadval hozir `POST /schedules` orqali beriladi. |
| `language` | **e'lon qilingan, iste'mol qilinmaydi** | Agent javob tili shu maydondan emas, persona matnidan kelib chiqadi. |
| `memory.scope` | **e'lon qilingan, iste'mol qilinmaydi** | `memory.put` / `memory.search` doim agent-scoped ishlaydi. |
| `tool_policy` (`prefer_api`) | **e'lon qilingan, iste'mol qilinmaydi** | Tool tanlashga ta'sir qilmaydi. |
| `layout` (`radial_map`) | **e'lon qilingan, iste'mol qilinmaydi** | UI bu qiymatni o'qimaydi. |
| `theme` | **e'lon qilingan, iste'mol qilinmaydi** | UI bu qiymatni o'qimaydi. |

Iste'mol qilinmaydigan maydonlarni pack'ga yozish zarar qilmaydi, lekin **xulqni
o'zgartirmaydi** — ularga tayanib xavfsizlik yoki biznes qaroriga rejalashtirmang.

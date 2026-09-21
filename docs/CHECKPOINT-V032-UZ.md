# v0.3.2: natijaga tayanuvchi agent loop source checkpointi

**SOURCE_ONLY / NOT_RUN / PRODUCTION NO-GO.** Sana: 2026-09-14.

Foydalanuvchi implementatsiya tartibini agentga topshirdi. «Faqat kod va review» chegarasi saqlandi: hech qanday loyiha testi, test collection, build, typecheck, lint, compile, demo, CI, deployment yoki live provider chaqiruvi bajarilmadi. v0.3.1 tuzatishlari ushbu ZIP ichida saqlangan. Bu to‘liq PRD yoki production mahsulot yakuni emas.

## Yozilgan ish oqimi

Autentifikatsiyadan o‘tgan owner/operator agent va topshiriq bilan `agent-run` yaratadi. Server tenant, actor, qadam limiti, call limiti va deadline’ni SQLite’da saqlaydi. Worker bitta model qarorini oladi, undan bitta oddiy engine task yaratadi va uning haqiqiy natijasini kutadi. Keyingi model qaroriga shu run’ning muvaffaqiyatli task natijalari beriladi. Oldindan yozilgan JSON placeholder yoki taxminiy tool natijasi ishlatilmaydi.

Model uchta shakldan bittasini beradi: bitta tool chaqiruvi, haqiqiy observation identifikatorlari bilan yakuniy javob yoki aniqlashtiruvchi savol. Yakuniy javob faqat dashboardda ko‘rsatiladi, avtomatik Telegram/Instagram jo‘natmasi emas. Mavjud literal planner/inbox yo‘li saqlanadi; barcha kanallar avtomatik yangi loopga o‘tkazilmadi.

## Source tarkibi

| Qism | Yozilgan o‘zgarish | Bajarilgan tekshiruv |
| --- | --- | --- |
| Persisted loop | `platform_runtime/agent_loop.py`, run/turn state, budget, cancellation, source references | Faqat kodni o‘qib review |
| Model adapter | `platform_runtime/agent_planner.py`, explicit opt-in, bounded context, strict JSON, no retry | NOT_RUN |
| Engine | `_submit` transaction-aware ichki metod; linked task va parent-run fencing | Faqat diff/manual review |
| Worker | Mavjud task ijrosidan keyin bitta loop advancement | NOT_RUN |
| API | Create/list/detail/cancel agent-run endpointlari, strict request va directory ruxsatlari | NOT_RUN |
| Dashboard | `AgentRuns.tsx`, yangi tab, progress, task/approval havolalari, source IDs, cancellation | JSX/typecheck/build NOT_RUN |
| Test source | Loop, model adapter, directory revoke, HTTP input va endpoint regression ssenariylari | Yozilgan, yig‘ilmagan va ishga tushirilmagan |

## Chegaralar va invariantlar

Bir run ko‘pi bilan 12 tool qadamga ega; standart 6. Model chaqiruv rezervlari limiti qadamlar + 1. Standart deadline 1800 soniya, API oralig‘i 60..86400 soniya. Deadline tasdiq kutishni ham hisoblaydi. Bu muddat yangi dispatchni taqiqlaydi, allaqachon tarmoqqa uzatilgan so‘rovni fizik jihatdan bekor qilish kafolati emas.

Planner rezervi oldindan write transactionda yoziladi. Lease 60 soniyadan yoki qolgan deadline’dan oshmaydi. Boshqa worker xuddi shu run uchun ikkinchi faol planner rezervini olmaydi. Process uzilib qolsa yoki javob kechiksa, rezerv avtomatik qaytarilmaydi: run escalated bo‘ladi. Haqiqiy provider qayta chaqirilgani haqida da’vo yo‘q; bu holatlarni tasdiqlaydigan test source yozilgan, bajarilmagan.

Task yaratish, run bilan bog‘lash, qadam hisoblagichi va audit bir SQL transactionda yoziladi. Ichki savepoint invalid qaror paytida yarimta yoki run’ga bog‘lanmagan yangi task qolishini oldini olish uchun qo‘shildi. Provider chaqiruvi SQL transaction ichida emas.

`agent` kanali directory a’zoligini tekshiradigan kanallarga qo‘shildi. Creator authority model oldidan va javobni qabul qilishda tekshiriladi. Linked task approval, claim va dispatchda parent status/deadline’ni tekshiradi. Freeze yangi model/tool dispatchini to‘xtatadi; approved write allaqachon yuborilgan bo‘lsa uning natijasi uncertain bo‘lib qolishi mumkin.

Har qanday write tool avvalgi engine approval qoidasida qoladi. Model role, tenant, approval, device yoki budjetni o‘zgartira olmaydi. Local runner toollari bu loop uchun yopiq. Outbound recipient pack allowlist va engine qoidalari bilan cheklanadi. Bir xil tool va aynan bir xil argumentlar takrori bloklanadi; bu ataylab qayta tekshirish kerak bo‘ladigan legitim so‘rovlarga ham cheklov qo‘yadi.

Failed/cancelled/uncertain taskdan keyin yangi model qarori yoki avtomatik write retry bo‘lmaydi. Uncertain task keyin owner tomonidan reconcile qilinsa ham loop o‘zicha qayta boshlanmaydi. Operatorning yangi topshirig‘i kerak.

## Dalil va model javobi haqida aniq chegaralar

Model faqat shu run’ning `succeeded` task/step natijalarini observation sifatida oladi. Har bir observation argumentlari va natijasi bilan 12000 UTF-8 baytdan, butun history 48000 baytdan, adapter konteksti 64000 baytdan oshmasligi kerak. Haddan katta natija yashirin kesilmaydi; run escalation orqali to‘xtaydi. System prompt bu context limitidan tashqari qo‘shiladi.

Final javobdagi `evidence_ids` haqiqiy muvaffaqiyatli observationlarga tegishli ekanligi strukturaviy tekshiriladi. **Har bir jumlaning semantik to‘g‘riligi, raqamlar hisob-kitobi yoki provider natijasining tashqi haqiqatga mosligi avtomatik isbotlanmaydi.** API `semantic_fact_check=not_performed` deydi; UI ham buni ochiq ko‘rsatadi. Bu hallucination yo‘qligi kafolati emas.

Tool natijalarida PII yoki boshqa mijoz ma’lumoti bo‘lishi mumkin. `llm.agent_loop_enabled` konfiguratsiyasi **standart holatda false**. Operator data-sharing siyosatini belgilamaguncha yangi result-fed adapter yoqilmaydi. Mavjud LLM model va key reference konfiguratsiyasi qayta ishlatiladi; hech qanday haqiqiy kalit paketga qo‘shilmadi. Universal secret/PII redaction yoki prompt-injectiondan 100% himoya implementatsiya qilingan deb da’vo qilinmaydi.

Model request uchun `max_tokens=1600` yozildi. Model chaqiruvlari va bayt limitlari **haqiqiy token xarajati yoki pul sarfi budjeti emas**. Provider usage/pricing asosidagi spend ledger hali ochiq. Worker arxitekturasi hamon ketma-ket; load/failure va fairness acceptance bajarilmadi.

## API va UI kontrakti

`POST /platform/{tenant}/agent-runs` kirishi: `agent`, `key`, `text`, ixtiyoriy `max_steps` va `max_seconds`. Faqat owner/operator. Bir xil key va bir xil input bir xil run ID qaytaradi; o‘zgargan payload yoki actor conflict beradi. `accepted=true` execution muvaffaqiyatini bildirmaydi. Endpoint model yoki toolni inline ijro qilmaydi.

`GET /platform/{tenant}/agent-runs` so‘nggi 100 run’ni beradi. `GET /platform/{tenant}/agent-runs/{id}` detail, task timeline va source reference’larni beradi. Tenant boundary saqlanadi; planner nonce’lari qaytarilmaydi. `POST .../{id}/cancel` joriy owner/operator authority bilan ishlaydi va frozen workspace’da ham mavjud.

UI raw HTML yoki model bergan URLni executable havolaga aylantirmaydi; model matni React text sifatida ko‘rsatiladi. Network xatosidan keyin avtomatik POST retry yo‘q. Bir panel ichida foydalanuvchining qo‘lda qayta urinishi o‘zgarmagan payload uchun idempotency key’ni saqlaydi; panel unmount/reload bo‘lgandan keyin bu xotira kafolatlanmaydi. Run va task holatini qayta ochib tekshirish mumkin. Eski task cancel tugmasi ham frozen holatda to‘xtatish ruxsatiga moslashtirildi.

## Schema va orqaga moslik cheklovi

Engine schema’ga `p_agent_runs`, `p_agent_turns` va indeks qo‘shildi; `p_migrations` version 2 marker yozadi. Jadval yaratish `CREATE TABLE IF NOT EXISTS` usulida, eski user/customer/task satrlarini o‘chirish kodi qo‘shilmadi. Ammo haqiqiy migration yoki restore **bajarilmadi**.

**Mixed-version worker va rollback xavfi ochiq:** v0.3.1 va undan eski engine parent-run fence’ni bilmaydi. Yangi loop tasklari mavjud paytda eski worker ishlashi yoki kodning orqaga qaytarilishi bu cheklovni chetlab o‘tishi mumkin. Shu sabab migration/rollback, drained-work holati va barcha workerlarning mosligi operator tomonidan tasdiqlanmaguncha production NO-GO. Agent deployment yoki rollback amalga oshirmadi.

## Ochiq backlog

Identity request-transaction session fencing, OIDC/MFA/email/reset, production vault, provider-specific CRM/ERP OAuth/sync/write/reconcile, barcha kanallar uchun result loop, spend ledger, to‘liq admin/billing, observability, production dependencies/lock va PRDning boshqa ochiq qismlari tugallanmagan. Regression test source’ining o‘zi correctness dalili emas.

Tarixiy v0.3 PASS va v0.3.1 source checkpointi yangi versionni tasdiqlamaydi. Joriy status `docs/verification/v032/`da. Bu bosqichda «barcha buglar tuzatildi» yoki «100% tayyor» degan da’vo qilinmaydi.

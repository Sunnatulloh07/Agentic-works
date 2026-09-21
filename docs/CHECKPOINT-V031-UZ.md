# v0.3.1: control-plane kod va review checkpointi

**Holat: SOURCE_ONLY / NOT_RUN / PRODUCTION NO-GO.** Sana: 2026-09-14.

Bu foydalanuvchi taqdim etgan Google Drive ZIP’dagi v0.3 source’ining davomidir. Bu to‘liq mahsulot implementatsiyasi, pentest, mustaqil audit yoki release tasdig‘i emas. Foydalanuvchi barcha bajariladigan sinovlarni o‘ziga oldi. Agent loyiha kodini, test, build, typecheck, lint, compile yoki demo buyruqlarini bajarmadi. Ushbu hujjatning da’volari kodni o‘qish va matnli diff reviewga asoslangan.

## Tuzatilgan source yo‘llari

| Qism | O‘qishda aniqlangan bo‘shliq | Yozilgan o‘zgarish |
| --- | --- | --- |
| Cancel | HTTP ruxsat tekshiruvidan keyin a’zolik bekor qilinsa, engine mutationda qayta tekshirmagan | Write transaction ichida joriy owner/operator a’zolik tekshiriladi |
| Reconcile | Service tashqaridan berilgan `role` qiymatiga tayangan | Write transaction ichida joriy owner tekshiruvi; dalil turi va 500 belgilik limit |
| Freeze/resume | Engine owner huquqini tekshirmagan, HTTP xatosi 500 bo‘lishi mumkin edi | Transaction ichida owner tekshiruvi; API `call` orqali 403 mapping; exact boolean |
| Device enrollment/revoke | Freeze check, device generation va audit uch xil transactionga ajralgan | Owner, freeze, generation, task/approval fencing va audit bitta `BEGIN IMMEDIATE` transactionda |
| Device approval lifecycle | Rotatsiya qilingan device tasklari bekor bo‘lsa ham approval pending/approved qolgan | Tegishli approval rejected, actor va decided bilan yopiladi; lease tozalanadi |
| Inbox retry | Asl event kanali operatorning joriy huquqini tekshirish o‘rnini bosa olmagan | Retry operatori va web/cron eventning asl yuboruvchisi transaction ichida tekshiriladi |
| Idempotent replay | Avvalgi task/event mavjud bo‘lsa, ruxsat qayta tekshirilmasdan javob qaytgan | Directory authority dedup javobidan oldin tekshiriladi |
| Customer 360 | Bo‘sh `actor` yashirin trusted-internal bypassga aylangan | Directory rejimida actor majburiy; active user/workspace/membership tekshiriladi |
| HTTP input | Ayrim bool/int inputlar coercion va qo‘shimcha fieldlarni qabul qilgan | Umumiy strict, extra-forbid model; decision/outcome literal; retry input chegaralari |
| Service input | Schedule floatni qabul qilgan; event obyekt bo‘lmasa tasodifiy xato | Exact integer interval; event dict va bounded identity; device ID va bool tekshiruvi |

## Saqlangan xavfsizlik semantikasi

Faol owner/operator frozen workspace’da cancellation qila oladi. Faol owner frozen workspace’da reconcile, device revoke va unfreeze qila oladi. Freeze paytida yangi device enrollment rad etiladi. Suspended workspace ushbu directory tekshiruvlaridan o‘tmaydi. Freeze va workspace suspension bir xil holat emas.

Avval qabul qilingan task/event uchun ruxsatli foydalanuvchining frozen replay javobi read-only qoladi. Revoked, disabled yoki suspended directory authority replay orqali qayta ochilmaydi. Task read API’lari hanuz tenant-level ruxsat modelida, individual task egaligiga o‘tkazilmadi.

Tashqi write allaqachon yuborilgan bo‘lsa, bekor qilish uni orqaga qaytarish kafolati emas. `uncertain` va operator evidence bilan reconcile saqlanadi. API initial session verification mavjud, lekin ushbu checkpoint har bir control mutation transactioniga session ID/expiry fencing qo‘shgan deb da’vo qilmaydi. Membership ruxsatining yangiligi va interaktiv sessionning yaroqliligi alohida chegaralar.

Standalone `Engine(..., authority=None)` dependency-free runtime/test kontrakti sifatida saqlanadi. Production API `runtime_authority` callbackini uzatadi. Bu kutubxona konstruktorini barcha embeddinglar uchun production-fail-closed qilib qayta loyihalash emas.

## Test source va fixture o‘zgarishlari

`api-python/runtime_tests/test_control_plane_authority.py` yangi service-level negative va positive ssenariylarni beradi. `api-python/integration_tests/test_control_plane_hardening_http.py` HTTP strict input, authdan keyingi demotion/disable, frozen revoke va audit kontraktlarini beradi. Bu fayllar **yozilgan, ishga tushirilmagan**, mock yoki haqiqiy provider natijasi olinmagan.

Eski Customer 360 fixturelari anonymous yozuv o‘rniga haqiqiy fixture owner va workspace yaratadigan qilib moslashtirildi. Bir belgilik test workspace `t` directory ID talabiga mos `tt`ga o‘zgardi. Eski tenant isolation, order reassignment va freeze assertionlari olib tashlanmadi. Auth’ni o‘chirib testni yashirish yo‘li ishlatilmadi.

Kod yozilgach o‘zgargan mutationlar, call-site’lar, transaction tartibi, test setup/cleanup va API error mapping matnli reviewdan o‘tkazildi. Bu kompilyatsiya, lint, test collection yoki bajarilgan regressiya emas.

## Hali ochiq ishlar

Result-grounded agent loop, bounded spend/time budget, OIDC/MFA/email verification/reset, production vault, to‘liq service identity va request-transaction session fencing, live CRM/ERP adapterlari, OAuth/sync/write/reconcile, usage/billing/admin UI, observability va PRDning boshqa ochiq qismlari ushbu checkpointda tugatilmadi.

Next/React va Python dependency pin/lock/security review bu bosqichda o‘zgartirilmadi. Yangilangan dependency/advisory ma’lumoti olinmadi. HTTP/UI/CI, live PostgreSQL/CRM, load/fault, backup/offsite, migration rollback va production acceptance foydalanuvchida qoladi. Yangi DB schema migration talab qiladigan jadval/ustun qo‘shilmadi; bu deployment yoki migrationning tekshirilganligini anglatmaydi.

`docs/verification/v03/` tarixiy dalildir. Uning PASS raqamlari v0.3.1 uchun yaroqli gate emas. Joriy holat `docs/verification/v031/execution.json` va `release-evidence.json`da. Test natijasi yoki exit code o‘ylab topilmadi.

## Davom ettirish

Joriy ish tartibi `WORKING-AGREEMENT-UZ.md`da. Keyingi modulning ko‘lami foydalanuvchi bilan tanlanadi. CRM uchun provider nomi va versiyasi, identity uchun identity-provider/vault tanlovi kerak bo‘ladi. Hech qaysi bosqichda chatga live secret yuborish talab qilinmaydi.

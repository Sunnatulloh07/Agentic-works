# 0.1 engineering preview o‘zgarishlari

Oldingi arxiv statik ko‘rib chiqildi. Oldingi AI xulosalari avtomatik fakt deb olinmadi: runner/exec permission bypass, queued resend, tenant-only JWT, erta dedup, unconnected Telegram sender va faqat metadata orchestration muammolari kodda mavjud edi.

Yangi standart oqim: verified channel -> durable inbox -> worker planner -> policy-validated immutable task/steps -> approval -> atomic claim -> executable tool -> durable result/audit. Web typed plans va cron ayni engine’dan o‘tadi. Noaniq external write replay qilinmaydi.

Qo‘shildi: engine va schema v1, typed executable registry, records, scoped memory, summaries, interval schedules, MCP transport, provider adapters, configured JSON LLM planner, role/device tokens, control-plane API, worker, UI, Linux-only safe read runner, verification suite, runbooks va Docker/CI configuration.

Legacy runner endpointlari retired. Default platform rejimida eski mutation endpointlar o‘chiq. Legacy order approval uchun stable order ID qo‘shildi; lekin bu eski suite’ni yangi release acceptance o‘rniga qo‘ymaydi. Oldingi simulated physical tools yangi runnerga ulanmagan.

Mustaqil statik review asosida quota, approver_role, external recipient authorization, Meta account routing, device re-enrollment invalidation, freeze fencing, journal fsync va filesystem descriptor tekshiruvi kuchaytirildi. Mos regression testlar qo‘shildi.

Full PRD tugallanmagan. Amaldagi aniq status va yo‘q qismlar `IMPLEMENTATION-STATUS.md` da.

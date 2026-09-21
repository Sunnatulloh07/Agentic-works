# v0.3 qayta audit va tuzatishlar

**Natija: mahalliy hardening checkpoint. Production: NO-GO.**
Bu uch xil mahalliy tekshiruv yo‘nalishi, mustaqil pentest yoki production sertifikat emas.

## 1-pass: xavfsizlik va regressiya

P0/P1 topilmalar: ochiq bootstrap; o‘zicha workspace/plan tanlash; refreshga bog‘lanmagan access JWT; eski refresh takror ishlatilganda descendant sessionning qolishi; optional membership enforcement; processlararo read-check-write race. Tuzatish: admin-gated bootstrap, trusted provisioning, account/workspace token separation, session ID va version fencing, family revocation, production fail-closed, BEGIN IMMEDIATE + nested savepoint, hashed tokens, audit.

Avvalgi test `old refresh rejected, replacement still usable` xatti-harakatini kutgan edi. Bu xavfsizroq replay siyosatiga mos emasligi uchun talab yangilandi va alohida negative hamda multiprocessing testlar qo‘shildi. Testni shunchaki yashirish yoki xatoni ignore qilish amalga oshirilmadi.

Topilgan Customer 360 order reassignment tuzatildi, freeze va mutation audit qo‘shildi. Oxirgi owner himoyasi transaction ichida tekshiriladi.

## 2-pass: route, UI, worker va connector konformansi

Identity POST endpointlari oldin middleware tomonidan 410 qaytargan. `/identity/` explicit ajratildi, session validation umumiy JWT verification orqali eski consumerlarda ham ishlaydi. HTTP testlar kodga qo‘shildi, ammo dependencylar bo‘lmagani uchun mahalliy o'tkazilmadi.

Dashboarddagi qo‘lda JWT kiritish login/invite/workspace oqimi bilan almashtirildi. Tokenlar browser xotirasida, refresh single-flight, xatoda POST avtomatik qaytarilmaydi. JSX syntax tekshirildi, React typecheck/build o‘tdi deyilmaydi.

Worker membership revoke’dan keyin queued taskni bajara olgan edi. Task creator va approver claim/dispatchda qayta tekshiriladi; event sender planner oldidan tekshiriladi; schedulelar ownerga bog‘lanadi. Suspended workspace yangi ish bajarmaydi. Allaqachon yuborilgan external write natijasi xavfsiz uncertain/reconcile bilan boshqariladi, uni avtomatik undo qilish da’vo qilinmaydi.

PostgreSQL config oldin hostni o‘ziga default allowlist qilgan va `sslmode=require`ga ruxsat bergan. Endi explicit host allowlist, verify-full, explicit schema va isolation talab qilinadi. Column/filter cheklovi, base-table metadata va bounded cursor contract-test bilan qoplangan. `healthy` configga ishonish olib tashlandi. Probe natijasi faqat single-table read tekshiruvi deb qaytariladi.

Ikkinchi o‘qishda topilgan qo‘shimcha xatolar: schedule rad etilganda ham submitted count oshishi, strict bo‘lmagan verified/total_minor HTTP input, malformed connector config uchun tasodifiy 500, libpq dbname orqali conninfo expansion. Hisoblash va input/config validatsiyasi tuzatildi. SQLite fixture connection leaklari yopildi.

## 3-pass: PRD, migratsiya, deliverable va qayta test

PRD talablari implementatsiya/test holati bilan jadvalga bog‘landi. Original, v0.2 va v0.3 dalillari ajratildi. Eski snapshotdagi 114/119/125/129 test raqamlari tarixiy, joriy gate emas. Oldingi berilgan arxiv connector bosqichidan avval paketlangan edi; yangi v0.3 arxiv barcha joriy source o‘zgarishlarini o‘z ichiga oladi.

Yangi migratsiya eski sessionlarni bekor qiladi, user/workspace/customer data saqlanadi. WAL-aware snapshot va yangi offline pathga restore lokal testdan o‘tadi. Encrypted offsite restore va production migration rollback bajarilmagan.

Arxiv manifesti, o‘zgargan fayllar ro‘yxati, test stdout/stderr, dependency inventory va NO-GO evidence alohida saqlanadi. Yakuniy arxiv chiqarilgach uning source’idan runtime/Node/tooling testlari qayta bajariladi. Hech qanday live secret, .env, DB, __pycache__, node_modules paketga kiritilmaydi.

## Hali ochiq release blockerlar

- HTTP integration testlar bu Computer’da bajarilmaydi: FastAPI/Pydantic/PyJWT/PyYAML/httpx/pytest/redis yo‘q.
- React/Next dependencies yo‘q. JSX syntax yoki session client Node testi production UI build o‘rnini bosa olmaydi.
- Paketdagi Next 14.2.5 production uchun tasdiqlanmagan. Yangi versiya/advisory ma’lumoti ushbu passda internetda qayta tekshirilmadi, lockfile taxmin bilan o‘zgartirilmadi.
- Python requirements hali keng version-range ko‘rinishida. Reproducible lock va supply-chain review zarur.
- CRM/ERP live OAuth/sync/write/reconcile va PostgreSQL live staging testlari yo‘q.
- OIDC/MFA/vault, keng admin/billing, result-grounded agent loop, monitoring, load/failure va production pilot yakunlanmagan.
- Bu tekshiruv barcha buglar yo‘qligini yoki 100% xavfsizlikni isbotlamaydi.

## Yakuniy mahalliy hisob

- Python runtime: 181 passed.
- Backup/restore va release tooling: 3 passed.
- Node runner: 15 passed.
- Browser session client: 8 passed.
- Python compile, JSX syntax-only va session declaration typecheck: exit 0.
- Offline deterministic demo: bajarildi, haqiqiy provider emas.
- FastAPI HTTP va React/Next production build: NOT_RUN.

Aniq buyruqlar va exit-code dalili `verification/v03/execution.json`da. Archive-source rerun dalili arxiv bilan beriladigan alohida verification faylida bo‘ladi.

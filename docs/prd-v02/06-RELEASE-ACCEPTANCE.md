# PRD-06: production qabul mezonlari

## Tayyorlik ta’rifi

“100% tayyor” degan belgi implementatsiya, test, xavfsizlik, deploy va real biznes ish oqimining dalillari bilan beriladi. Ushbu paket `0.2.0-preview`; productionga ruxsat emas. Unit testning o‘tishi frontend yoki haqiqiy CRM/STT/TTS ishlaganini isbotlamaydi.

## Bosqichlar

**G0, source va local core:** arxiv xavfsiz tekshiriladi, original saqlanadi, audit va PRD traceability tuziladi. Tenant isolation, approval va lokal connector regression testlari o‘tadi. Ushbu paket G0ning amalga oshirilgan qismi.

**G1, identity va data foundation:** haqiqiy login, membership/revoke, customer model, DB migration, secret vault, bitta foydalanuvchi tanlagan CRM va DB adapteri. Ikki tenantli end-to-end test majburiy.

**G2, bajaruvchi agent va biznes pilot:** grounded multi-step loop, budget, real read/write, provider idempotency/reconcile, customer dashboard. Bitta vertical va bir nechta takrorlanuvchi real ish bilan acceptance.

**G3, voice pilot:** rozilikli Uzbek eval corpus, TTS/STT narx sifati, streaming gateway, barge-in/handoff, privacy, concurrency va billing sinovlari. Tayyorlik endpoint bo‘yicha alohida belgilanadi.

**G4, production:** dependency/security review, load/failure test, offsite encrypted backup + restore, migration rollback, incident runbook, monitoring, TLS/egress, real provider staging va pilot kuzatuvi.

## Release checklist

| ID | Gate | Hozir |
|---|---|---|
| RL-01 | Core Python runtime tests | 114 passed, lokal |
| RL-02 | Node read-only runner tests | 15 passed, lokal |
| RL-03 | Python syntax | compileall passed |
| RL-04 | UI sintaksis | TS7 --noCheck syntax-only passed, typecheck emas |
| RL-05 | FastAPI HTTP integration tests | BLOCKED, dependencies mavjud emas |
| RL-06 | React typecheck va Next production build | BLOCKED, React/Next deps mavjud emas |
| RL-07 | Real Aisha/CRM/DB/LLM external integration | NOT_RUN; hech qanday pullik call bajarilmagan |
| RL-08 | Streaming latency, Uzbek accuracy va load | NOT_RUN |
| RL-09 | Production deploy, restore, rollback | NOT_RUN |
| RL-10 | Dependency security upgrade | BLOCKED; Next14.2.5ni deploy qilishga ruxsat yo‘q |

Computer’da FastAPI, Pydantic, PyJWT, httpx, redis, pytest, PyYAML va frontend dependencies yo‘q. Paket o‘rnatish va outbound provider ishlatish bajarilmadi. To‘liq testlarni foydalanuvchi CI yoki tayyor development muhitida bajarish kerak. Yangi HTTP testlar qo‘shilgan, o‘tdi deb belgilanmagan.

## Kritik release blockerlar

Next.js 14.2.5 eskirgan advisory range ichida. Middleware bypass mavjudligi bu appda isbotlanmagan, chunki auth FastAPI’da; ammo App Router security patch va boshqa advisory shartlari tekshirilishi kerak. Next14.2.35 eski 2025 RSC tuzatish liniyasi, 2026 avgustdagi ayrim critical patchlar 15.5.24/16.3.3 liniyalarida. Bu versiyalarni tekshirmasdan package-lockni qo‘lda almashtirish bajarilmadi. Supported versiyaga deliberate upgrade, lock regeneration va clean build shart.

Rasmiy dalillar: https://github.com/advisories/GHSA-f82v-jwr5-mffw ; https://nextjs.org/blog/security-update-2025-12-11 ; https://nextjs.org/blog/august-2026-security-release . Har advisory applicability alohida ko‘riladi, exploit bo‘ldi degan da’vo yo‘q.

## Xavfsiz ishga tushirish va rollback

Original ZIP va yangi ZIP alohida saqlanadi. Avval staging copy’da backup olinadi. v0.2 yangi DB migratsiya qo‘shmadi; oldingi schema saqlanadi. `ALLOW_INSECURE_DEV` default false; `ENV=dev`ning o‘zi ma’lum secret yoki anonim adminni yoqmaydi. CI test fixture’lari explicit insecure test flag bilan ajratilgan. Real serverda unique JWT/admin/webhook secretlar kerak.

MCP config endi `tool_schemas` talab qiladi. Bu breaking security configuration change: eskicha faqat `allowed_tools` yozilgan config tarmoq chaqiruvidan oldin rad etiladi. Config/example/runbookni birga ko‘chiring. Freeze va approval siyosati o‘zgargani sabab queued approvals policy fingerprint o‘zgarsa safe-fail bo‘lishi mumkin; yangi task va fresh approval yarating.

Rollback original code/config/backupga qaytishni anglatadi, ammo eski xavfsiz bo‘lmagan auth defaultini productionda qayta yoqish mumkin emas. Asl arxiv umumiy audit baseline, tavsiya etiladigan production release emas.

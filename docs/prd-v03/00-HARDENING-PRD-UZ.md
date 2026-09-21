# PRD v0.3: Identity, tenant va connector xavfsizlik bosqichi

Holat: **HARDENING_PREVIEW / PRODUCTION NO-GO**. Sana: 2026-09-14 (foydalanuvchi vaqti).
Bu oldingi universal biznes platformasi maqsadini saqlaydi, lekin barcha CRM/ERP adapterlarini tayyor deb e'lon qilmaydi.

## Muammo va ko‘lam

Oldingi snapshotda `/identity` POST yo‘llari legacy middleware tomonidan 410 bilan bloklangan edi. Bootstrap ruxsatsiz ochiq edi, JWTlar refresh-sessionga bog‘lanmagan, workspace nomlari orqali boshqa packni egallash xavfi bor edi. Refresh token takror ishlatilishi rad etilgan, ammo yangi avlod sessionlari o‘chirilmagan edi. Bu muammolar avvalgi oddiy unit testlar bilan ushlanmagan.

Ushbu bosqichning maqsadi: session-bound authorization, operator nazoratidagi provisioning, transactional membership, UI orqali haqiqiy login/workspace tanlash, worker ijrosida qayta authorization, Customer 360 yaxlitligi va PostgreSQL read-only chegaralarini mustahkamlash.

## Rollar va asosiy oqim

Platform operator packni serverda tekshiradi va lokal CLI yoki admin-token bilan cheklangan endpoint orqali workspace provision qiladi. Foydalanuvchi parol bilan kiradi, account-scoped session orqali o‘z workspace ro‘yxatini oladi va faqat active membership bor workspace’ni tanlaydi. Owner bir martalik taklif yuboradi. Yangi foydalanuvchi ro‘yxatdan o‘tishi uchun taklif tokeni, mos email va parol kerak. Oddiy foydalanuvchi plan yoki pack nomini egallay olmaydi.

Taklif tokenini yetkazish bu versiyada operator zimmasida. Email provider verification, MFA va OIDC hali yo‘q. Password login pilot foundation, production identity qabulidan o‘tmagan.

## Talablar va dalil

| ID | Talab | Kod / test | Holat |
|---|---|---|---|
| ID-301 | Bootstrap default yopiq, admin va flag talab qilinadi | identity_api, test_identity_http | SOURCE_ONLY_HTTP |
| ID-302 | Birinchi user + workspace atomik | identity_store.bootstrap_identity, multiprocessing test | LOCAL_PASS |
| ID-303 | Invite email, expiry, issuer authority va single-use | register_with_invitation, identity_hardening | LOCAL_PASS |
| ID-304 | 15 minutlik session-bound access JWT | auth, identity_api, HTTP testlar | SOURCE_ONLY_HTTP |
| ID-305 | Logoutdan so‘ng access va refresh rad qilinadi | authorize_claims, identity_hardening | LOCAL_PASS_SERVICE |
| ID-306 | Refresh replay butun family’ni bekor qiladi | rotate_session, multiprocessing test | LOCAL_PASS |
| ID-307 | Disabled user, suspended workspace, membership version fencing | authorize_claims, identity_hardening | LOCAL_PASS_SERVICE |
| ID-308 | IP va account rate-limit, secret input echo qilinmaydi | throttle + HTTP testlar | PARTIAL_LOCAL |
| ID-309 | Production directory rejimi o‘chirib bo‘lmaydi | directory_enabled | LOCAL_PASS |
| UI-301 | Login, invite registration, workspace tanlash, logout | SessionGate + platform page | SOURCE_AND_SYNTAX_ONLY |
| UI-302 | Memory-only token, single-flight refresh, POST retry yo‘q | session-client.mjs, 8 Node test | LOCAL_PASS |
| EX-301 | Queued task, approval va dispatchda active authority | runtime_authority, 9 test | LOCAL_PASS |
| EX-302 | Revoke qilingan sender uchun planner chaqirilmaydi | process_event authority | LOCAL_PASS |
| EX-303 | Schedule owner bilan bog‘langan, legacy unowned schedule yopiq | p_schedule_owners, tests | LOCAL_PASS |
| C360-301 | Order boshqa customerga ko‘chib ketmaydi | add_order, negative test | LOCAL_PASS |
| C360-302 | Freeze customer mutationni bloklaydi, audit atomik | customer360 + identity audit | LOCAL_PASS_SERVICE |
| PG-301 | verify-full TLS, explicit host/isolation/table/column | postgres_connector, 16 contract tests | CONTRACT_PASS_NOT_LIVE |
| PG-302 | Schema-qualified query, parametrli tenant/filter, timeout | emitted SQL contract tests | CONTRACT_PASS_NOT_LIVE |
| PG-303 | Named cursor, base-table tekshiruvi, money precision, data limit | fake-driver tests | CONTRACT_PASS_NOT_LIVE |
| CN-301 | Configdagi healthy yozuvi real health sifatida olinmaydi | connector_contract | LOCAL_PASS |
| OP-301 | Versioned additive migration, eski sessionlar bekor | identity_schema | LOCAL_PASS |
| OP-302 | WAL-aware backup, private artifact, no overwrite, restore | sqlite_backup, 3 tooling tests | LOCAL_PASS_ONLY |
| OP-303 | Dalilsiz production GO berilmaydi | check_release.py | LOCAL_PASS |

## Chegaralar

Logout biznes tasklarini avtomatik bekor qilmaydi, u interaktiv sessionni bekor qiladi. Membership revoke yoki workspace suspension keyingi worker authority tekshiruvida ishni bloklaydi. Tashqi providerga allaqachon yuborilgan amalni orqaga chaqirish kafolatlanmaydi; uncertain/reconcile siyosati saqlanadi.

Customer 360 `verified=true` operatorning attestatsiyasi, provider tasdiqlagan telefon/email isboti emas. Avtomatik customer merge yo‘q. Kontakt/detail kolleksiyalari 100 yozuv bilan cheklangan; to‘liq pagination va katta biznes ledgeri keyingi bosqichdir.

PostgreSQL `dedicated_database` konfiguratsiyasi operatorning isolation majburiyatidir. Least-privilege role, DB RLS, DNS/egress policy, server sertifikat zanjiri va haqiqiy ikki-tenant live test shart. Har bir network request uchun tarmoq darajasidagi hard deadline va har bir DB katak uchun server-side bytes limit hali alohida acceptance talab qiladi.

## Keyingi majburiy fazalar

1. HTTP integration va React/Next buildni haqiqiy dependency muhitida bajarish, topilgan xatolarni tuzatish. Dependencylar uchun security review va supported Next versiyasiga lockfile bilan upgrade.
2. OIDC/MFA/email verification/reset, encrypted secret vault, admin service identity va request-time authorizationni barcha control-plane mutationlarda yakunlash.
3. PostgreSQL live read-only/RLS/TLS acceptance; MySQL, SQL Server, Oracle, MongoDB va custom HTTP adapterlarini alohida tekshirish.
4. Bitrix24, amoCRM/Kommo, 1C va boshqa CRM/ERP uchun OAuth, mapping, sync, webhooks, idempotent write va reconcile.
5. MCP credential routing va pinned schemas bilan live acceptance, result-grounded agent loop, spend/time budget, cancellation va escalation.
6. Usage/billing/control-plane, observability, load/fault test, encrypted offsite restore, staging pilot va production deployment.

Bu ro‘yxat product backlog, tayyor capability ro‘yxati emas.

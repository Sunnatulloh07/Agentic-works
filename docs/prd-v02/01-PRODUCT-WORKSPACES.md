# PRD-01: profil, workspace va vakolatlar

## Muammo va maqsad

Hozir foydalanuvchi dashboardga tenant ID va bootstrap JWT kiritadi. Bu SaaS login, yagona profil yoki workspace membership emas. Bitta foydalanuvchi bir nechta biznesda ishlashi mumkin; bir biznesda bir nechta foydalanuvchi bo‘ladi. Biznesning mijoz profili esa platformadagi foydalanuvchi profilidan alohida entity.

## Asosiy model

`users(id, auth_subject, display_name, locale, status)` global inson hisobini ifodalaydi. `workspaces(id, name, plan, status, region)` biznesni, `memberships(workspace_id, user_id, role, status, version)` a’zolikni ifodalaydi. OIDC subject provider issuer bilan birga unikal bo‘lishi kerak. Email tasdiqlanmasdan akkauntlar avtomatik birlashtirilmaydi.

`customers`, `contacts`, `channel_identities`, `conversations`, `orders` tenant-scoped biznes obyektlari. Kanal identity’sini bir mijozga birlashtirish operator tasdig‘i va merge auditini talab qiladi; telefon yoki bir xil ismning o‘zi yetarli emas.

## Talablar va acceptance

| ID | Talab | Qabul mezoni |
|---|---|---|
| ID-01 | OIDC login, tasdiqlangan identifikatsiya | Invalid issuer/audience, muddati o‘tgan session va CSRF rad etiladi |
| ID-02 | Workspace almashtirish | Server faqat active membershipdagi workspace’larni beradi; UI tenant inputi vakolat bermaydi |
| ID-03 | Invite va revoke | Bir martalik, expiryli invitation; revoke keyingi request va job dispatchda kuchga kiradi |
| ID-04 | RBAC | Owner, operator, integrator, viewer va alohida platform-admin policy matrix testlanadi |
| ID-05 | Owner himoyasi | Oxirgi owner o‘zini o‘chira olmaydi; ownership transfer qayta autentifikatsiya bilan |
| ID-06 | Session lifecycle | Refresh rotation, reuse detection, logout-all, qurilma/session ro‘yxati |
| ID-07 | Customer 360 | Kontakt, kanallar, suhbat va buyurtmalar bog‘langan; cross-tenant merge mumkin emas |
| ID-08 | Tenant isolation | Ikki tenant bilan API, worker, cache, storage, exports va search negative testlari |

Operator topshiriq yaratadi va ruxsat etilgan approvalni beradi. Integrator connector sozlaydi, ammo moliyaviy write yoki role grant olmaydi. Viewer o‘qiydi. Platform admin mijoz tenant owner tokeni sifatida ishlatilmaydi. Break-glass access reason, expiry, qo‘shimcha approval va audit bilan cheklanadi.

## Ushbu implementatsiya chegarasi

Yangi `/platform/{tenant}/identity` tekshirilgan JWT’dan role va freeze holatini qaytaradi. UI shu server roliga qarab tugmalarni boshqaradi va logoutda avvalgi tenant state’ini tozalaydi. Bu ID-01..ID-08ning to‘liq implementatsiyasi emas. Bootstrap JWT eskirish muddati 24 soat; user-directory revocation hali yo‘q. Production identity uchun yangi migratsiya va OIDC provider kerak.

## Migratsiya qarori

Bootstrap subjectlar katalogga admin tomonidan explicit import qilinadi. Eski tokenlar avtomatik membership yaratmaydi. Legacy tokenlar uchun alohida muddatli migratsiya rejimi va key rotation rejalashtiriladi. Directory joriy qilinganda barcha backend, webhook mapping va worker yo‘llari bir xil policy boundary’dan o‘tishi shart.

## 2026-09-14 foundation implementatsiyasi

`app/identity_store.py` va `app/identity_api.py` quyidagi lokal directory foundationni beradi: password hash (scrypt), user, workspace, membership, one-time invitation, revoke, refresh-token rotation va logout. `IDENTITY_DIRECTORY=true` bo'lganda platform requestidagi role JWTdan emas, active membershipdan qayta tekshiriladi; revoke keyingi requestda darhol kuchga kiradi.

Endpointlar: `/identity/bootstrap`, `/identity/register`, `/identity/login`, `/identity/refresh`, `/identity/logout`, `/identity/workspaces`, invite accept va membership revoke. Raw refresh token faqat clientga qaytariladi, bazada hash saqlanadi.

Bu hali OIDC/JWKS, MFA, email verification, password reset yoki KMS-backed session vault emas. Productionda password bootstrap faqat first-run provisioning uchun, keyingi login OIDC va provider policy bilan almashtirilishi kerak.

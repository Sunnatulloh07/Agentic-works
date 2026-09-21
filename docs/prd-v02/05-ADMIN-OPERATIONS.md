# PRD-05: tenant va platform admin boshqaruvi

## Ikki xil admin

Tenant owner o‘z biznesi xodimlari, agent policy, connector, approval va xarajatini boshqaradi. Platform admin esa tenant lifecycle, xizmat holati, billing reconciliation, provider incident va release’larni boshqaradi. “Full admin” secretlarni ochiq ko‘rish yoki mijoz nomidan auditsiz biznes write bajarish degani emas.

| ID | Talab | Qabul mezoni |
|---|---|---|
| AD-01 | Tenant provisioning | Workspace yaratish, plan, region, suspend/resume va deletion workflow |
| AD-02 | Foydalanuvchilar | Invite, role grant, revoke, owner transfer va MFA/re-auth |
| AD-03 | Connector admin | Configure/test/rotate/revoke; secret masked, browserga qaytmaydi |
| AD-04 | Agent policy | Draft/publish/version/rollback; yangi riskli toolga explicit approval |
| AD-05 | Business observability | Task queue, latency, usage, delivery status, retry/DLQ, provider health |
| AD-06 | Billing | Usage ledger, kredit rezerv, invoice, refund/dispute, limit; ledger double-spend test |
| AD-07 | Break-glass | Sabab, muddat, owner/dual approval, banner, immutable audit |
| AD-08 | Audit eksporti | Tenant-scope, role, redaction, retention, export authorization |

## UI v0.2

Mavjud dashboardga serverdan tasdiqlangan rol va freeze holati, to‘liq local state logout, to‘g‘ri default agent, owner-only qurilma/freeze control va owner/integrator connector metadata paneli qo‘shildi. Backend rol tekshiruvi asosiy himoya bo‘lib qoladi. UI’ni yashirishning o‘zi security emas.

Role switch hisobga olinadi: viewer task yaratmaydi; integrator biznes task submit qilmaydi; operator owner-only device enrollment/revoke/freeze amallarini bajara olmaydi. Noaniq task uchun manual reconcile API saqlanadi. UI freeze paytida yangi write’larni bloklaydi; owner resume va device revoke qila oladi.

Bu admin SaaSning tayyor versiyasi emas. Login ekran o‘rniga JWT paste bor; workspace membership, billing, customer 360, secret vault, OAuth onboarding va platform-admin alohida console hali yozilmagan. Legacy UI eski admin token oqimi bilan qoldirilgan; production surface’dan olib tashlash yoki to‘liq migratsiya qilish kerak.

## Operational tamoyillar

Sog‘lom `/health` process tirikligini bildiradi, provider yoki biznes workflow tayyorligini emas. Readiness endpoint migration/configni tekshiradi; provider health asinxron alohida holat. Structured loglar secret va PII chiqarmaydi. Audit xatolik exception matnini emas, stable error code’ni saqlaydi. Single SQLite volume previewdan distributed productionga o‘tish alohida loyiha bosqichi.

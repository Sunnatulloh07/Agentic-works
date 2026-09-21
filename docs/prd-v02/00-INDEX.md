> v0.2 tarixiy hujjatlar. Joriy holat: `../prd-v03/00-HARDENING-PRD-UZ.md`.

# AI biznes platformasi: PRD v0.2

Holat: taklif va bosqichlangan implementatsiya shartnomasi. 2026-09-13 kuni berilgan source ZIP asosida. Bu hujjatlar eski talablarni yashirin almashtirmaydi; ziddiyatlar auditda qayd etilgan. Mahsulotning yakuniy maqsadi saqlanadi, ish esa tekshiriladigan release gate’larga ajratiladi.

| Hujjat | Yo‘nalish | Ushbu paketdagi holat |
|---|---|---|
| 01-PRODUCT-WORKSPACES.md | Profil, biznes workspace, RBAC, admin | Local user/membership/session foundation; OIDC/MFA hali yo‘q |
| 02-CONNECTORS-DATA.md | CRM, ERP, mijoz DB, secretlar, sync | SQLite + guarded PostgreSQL read-only foundation; CRM/ERP provider adapterlari alohida |
| 03-AGENT-EXECUTION.md | Agent ijrosi, approval, idempotency, budget | Runtime qattiqlashtirilgan; natijaga bog‘liq replanning qolgan |
| 04-VOICE-COST-QUALITY.md | O‘zbekcha STT/TTS, streaming, xarajat | Aisha REST adapterlari contract-test; live pipeline yo‘q |
| 05-ADMIN-OPERATIONS.md | Tenant admin va platform admin | Rolga mos preview UI; to‘liq control plane yo‘q |
| 06-RELEASE-ACCEPTANCE.md | Test, migratsiya, xavfsizlik, production | Local core testlar o‘tgan; production gate yopiq |
| 07-CUSTOMER-360.md | Mijoz, kanal identity, order va tenant-scope data | Local Customer 360 API + dashboard implementatsiya qilingan; CRM sync yo‘q |

Har requirement o‘z ID’siga ega. Status lug‘ati: IMPLEMENTED_LOCAL, CONTRACT_TESTED, SOURCE_ONLY, NOT_IMPLEMENTED, BLOCKED. UI’dagi chiroyli tugma yoki schema mavjudligi ishlaydigan integratsiya degani emas. Har connector uchun alohida autentifikatsiya, capability va real provider testi zarur.

Yakuniy scope: foydalanuvchi yagona hisobdan o‘z bizneslarini boshqaradi, ularga o‘z ma’lumotlarini ulaydi, agentlar faqat berilgan vakolat doirasida ishni bajaradi. Platforma admini xizmat operatsiyalarini boshqaradi, lekin mijoz secretlari va biznes ma’lumotlarini cheksiz ko‘rmaydi.

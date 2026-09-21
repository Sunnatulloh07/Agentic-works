# 2026-09-14 joriy yangilanish

Foydalanuvchi test, audit va review’ni bajarishga ruxsat berdi. Quyidagi eski faqat kod/review qoidalari tarixiy sessiyaga tegishli. Joriy ish bosqichma-bosqich davom etadi, real hisoblar, live mijoz ma’lumotlariga yozish va deployment alohida ruxsat talab qiladi. Joriy dalillar `verification/v033/` da.

---

# Ishlash kelishuvi

Sana: 2026-09-14. Foydalanuvchi tanlovi: **Faqat kod va review**.

Agent kod yozadi, kodni o‘qib review qiladi va test fayllarini tayyorlaydi. Agent test, build, typecheck, lint, compile, demo, benchmark, dependency installation, CI, deployment yoki live provider amallarini ishga tushirmaydi. Bajariladigan barcha sinovlar va deployment foydalanuvchida. Fayllarni o‘qish, tahrirlash, diff va ZIP/manifest tayyorlash sinov natijasi hisoblanmaydi.

Har bir bosqich uchun o‘zgarishlar, ochiq cheklovlar va NOT_RUN holati yoziladi. Oldingi arxiv test natijalari yangi source uchun PASS sifatida ko‘chirilmaydi. 100% bugsiz yoki to‘liq xavfsiz degan kafolat berilmaydi. Production NO-GO ochiq qoladi.

Ish saqlangan checkpointlar va keyingi aniq savol orqali davom etadi. Cheksiz fonda ishlash yoki suhbatdan tashqarida mustaqil davom etish va’da qilinmaydi. Parol, API key va boshqa live secret chatga yoki deliverable arxivga kiritilmaydi.

## Avtonom davom ettirish qarori

Foydalanuvchi shu suhbatda «bir boshidan full avtanom qilaver» deb, implementatsiya tartibini agentga topshirdi. Har kichik modul uchun qayta tanlov so‘ralmaydi. Faqat biznes talabi, haqiqiy CRM/ERP turi yoki integratsiya kontrakti kabi taxmin qilib bo‘lmaydigan ma’lumot so‘raladi. Bu qaror avvalgi «Faqat kod va review» chegarasini, product write approvalini, production NO-GO yoki secret taqiqini bekor qilmaydi.

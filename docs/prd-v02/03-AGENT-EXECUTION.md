# PRD-03: agentlar ishni ishonchli yakunlashi

## Natija modeli

Agent mavjudligi system prompt yoki reja chiqarish bilangina o‘lchanmaydi. Biznes vazifasi bajarilgan deyilishi uchun haqiqiy tool natijasi, kerakli approval va yakuniy tekshiruv bo‘lishi kerak. “Mijoz qo‘shildi” degan javob provider receipt yoki tekshirilgan record bo‘lmasa chiqarilmaydi.

| ID | Talab | Qabul mezoni |
|---|---|---|
| AG-01 | Typed tool execution | Har tool schema, risk, scopes va executable handlerga ega; noma’lum tool fail-closed |
| AG-02 | Natijaga bog‘liq loop | read → observation → keyingi plan → approval/write → verification → grounded answer |
| AG-03 | Budget | Max steps, vaqt, provider spend, parallel jobs, audio minutes; atomik reservation/settlement |
| AG-04 | Approval binding | Tenant, agent, args, target, tool/schema/policy version fingerprintga bog‘lanadi |
| AG-05 | Mustaqil tasdiq | Policy talab qilsa task yaratuvchi o‘z taskini approve qila olmaydi |
| AG-06 | Durable ijro | Idempotency, lease fencing, crash recovery, uncertain state va qo‘lda reconcile |
| AG-07 | Kill switch | Freeze yangi ish va approvalni bloklaydi, dispatchdan oldin qayta tekshiradi |
| AG-08 | Eskalatsiya | Yetishmagan vakolat, noaniq qiymat va provider failure inson queue’siga reason bilan boradi |

## v0.2 holati

Oldingi runtime task/step/approval/auditni SQLite’da saqlaydi, tenant-scope va idempotency bor. Barcha write tool’lar approval talab qiladi. Yangi kod freeze paytida yangi task, event, schedule va approve’ni rad etadi; oldingi idempotent replay o‘sha taskni qaytarishi mumkin. Qayta retry frozen holatda bloklanadi. Handler chaqiruvi oldidan yana local claim/freeze tekshiruvi bor.

Freeze allaqachon yuborilgan network requestni bekor qila olmaydi. DB tekshiruvi va tashqi request o‘rtasida mutlaq atomiklik yo‘q. Shu sabab noaniq write’lar `uncertain` bo‘ladi, ko‘r-ko‘rona retry qilinmaydi. Remote cancel va reconciliation providerga xos bo‘ladi.

Pack’da `approval.independent: true` qo‘yilsa creator o‘z write’ini approve qila olmaydi; mos roldagi boshqa actor kerak. Default backward-compatible `false`, bu barcha agentlarda ikki kishilik tasdiq yoqildi degani emas. Moliyaviy, destructive va fizik ishlar uchun production policy defaulti qat’iyroq bo‘lishi kerak.

## Qolgan asosiy ish

Hozir `llm.py` bir martalik literal-argument plan chiqaradi. Tool resultidan keyin iterativ replanning yo‘q. AG-02 va AG-03 tayyor emas. Har observation data sifatida olinadi, instruction sifatida emas. Bounded loop agentga yangi vakolat bermaydi; runtime har qadamni yana tekshiradi.

Agent loop testlari: qidiruvdan hech narsa chiqmasligi; ikki xil mijoz; duplicate create; CRM vaqtincha ishlamasligi; approval expiry; task cancellation; provider receipt yo‘qolishi; prompt injection; budjet tugashi. Har biri aniq expected outcome oladi. Scheduled jobs user membership revoke va tenant plan holatini dispatch paytida tekshirishi shart.

# v0.3.7 implementation va review qaydlari

## Scope

Bu checkpoint barcha PRDni emas, OAuth/Google/sync blokini davom ettiradi. Ish holati IN_PROGRESS. Hozircha foydalanuvchidan credential yoki real sinov talab qilinmaydi. Runtime provider/config/signing ma’lumotlari operator keyin kiritadigan boundary sifatida qoldirilgan.

## Yozilgan kod

`secret_vault.py`: cryptography AES-256-GCM; tasodifiy 96-bit nonce; tenant/connection/provider/account/generation/purpose/version/key ID authenticated data; qat’iy encrypted envelope; max 64 KB plaintext; versioned key ring va rewrap. Kalitlar avtomatik yaratilmaydi, loglanmaydi. Bu hostlangan KMS emas.

`oauth.py`: state SHA-256, encrypted PKCE verifier, S256, 600 sekund TTL, atomik bir martalik callback; owner/session-family/config/generation binding; account va configured scopes tekshiruvi; SQLite single-flight refresh; parallel caller uchun explicit conflict; `invalid_grant` reauthorization; ambiguous token outcome uchun no-blind-retry; stale refresh uchun explicit fencing. Local revoke vault buzilgan yoki config o‘zgarganda ham bajariladi. Remote revoke uchun alohida encrypted outbox va idempotent retry; pending revoke bo‘lsa reauthorization bloklanadi. Provider yangi token bergach identity/store/session tekshiruvi yiqilsa token cleanup queue’ga olinadi. Encryption ham buzilgan va provider tokeni allaqachon berilgan bo‘lsa cleanup cheklovi auditga yoziladi.

`google_oauth.py`: Google authorize/token/revoke/userinfo manzillari source’da qat’iy belgilangan; redirect yo‘q; timeout/response limit; OAuth client secret faqat deployment injection; DSEC header-only reference form body uchun rad etiladi. Expected Google subject operator tomonidan oldindan aniq sozlanadi. Bu application OIDC login yoki ID-token verification emas.

`google_adapters.py`: google.gmail.list/read/send, google.drive.list/metadata, google.calendar.list/create. Typed request schema, expected OAuth subject/scopes, agent allowed_connections, recipient/calendar allowlist. Barcha write engine approvaldan o‘tadi. Engine fingerprint OAuth config/generation/resource policyga bog‘langan. Active claim, current task actor, consumed unexpired approval va approver qayta tekshiriladi. Remote write oldidan durable journal. Timeoutda uncertain, blind retry yo‘q. Gmail Message-ID correlation xolos, exactly-once kafolati emas. Calendar stable event ID va provider receipt ID tekshiruvi. Provider read matni untrusted_content deb belgilanadi.

`sync_store.py`: real SQLite claim/lease, owner binding, credential generation fence, opaque cursor CAS, idempotent page receipt, record-version dedup, eskirgan eventni e’tiborsiz qoldirish, tombstone, page+records+cursorning atomik commit’i. Bu ichki service foundation. Gmail history, Drive changes va Calendar sync-token walkerlar hali alohida yoziladi. Revision monotonligini provider-specific mapping ta’minlashi kerak.

`app/oauth_api.py`: owner-only OAuth list/begin/complete/revoke/rewrap/recover/retry-revocations; access tokens brauzerga chiqmaydi; application session-family callback binding; provider call davomida logout bo‘lsa yangi credential store rad qilinadi. Konfiguratsiya/kalit yo‘q bo‘lsa ham emergency local disconnect mavjud.

UI: owner OAuth panel, popup callback, origin/source/state/expiry tekshiruvi; code va state saqlash faqat xotirada; callback query historydan tozalanadi; no-referrer/no-store javob headerlari; runtime expires_in validation; popup close/timeout/unmount handling. Pure JS boundary testlari real Node’da. React UI browserda hali bajarilmagan.

## Reviewlar va fixlar

Birinchi mustaqil source review: transient remote revoke yo‘qolishi va post-issuance token orphan bo‘lishi topildi. Encrypted revoke outbox, retry, cleanup va reauthorization block bilan tuzatildi. Ikkinchi review: UI TTL validation, dispatch context fingerprint, final fence, transaction ichidagi current policy recheck va status CAS kuchaytirildi. Callback no-referrer/no-store oldindan Next config’da bor edi, reviewga yuborilgan excerptda yo‘q edi. Final authority tekshiruvini qo‘shganda `approved` va `consumed` semantikalari nomuvofiqligi lokal regressionda topildi va `consumed` + expiry tekshiruvi bilan tuzatildi.

## Oldingi auditdagi xato

v0.3.6 `/runner/ws` routerni import qilgan, lekin applicationga ulab qo‘ymagan. Oldingi auditda exposed deb aytilgani noto‘g‘ri. Foydalanilmaydigan import olib tashlandi va non-registration regression yozildi. CORS PUT mismatch haqiqiy edi va tuzatildi.

## Chegaralar, yashirilmaydi

- Barcha development tugamagan; 100% yoki production GO deyilmaydi.
- Cryptography haqiqiy kutubxona bilan testlandi. Google transport javoblari fake. Real providerlar chaqirilmadi.
- Core baseline dependency-free emas: OAuth testlari uchun cryptography==50.0.1 kerak. CI core job pre-install qadamiga ega. Computer’da paket o‘rnatilmadi, mavjud versiya ishlatildi.
- HTTP pytest/FastAPI/Pydantic/PyJWT yo‘q. Yangi HTTP source testlar yozildi, NOT_RUN.
- Next/React typecheck/build/browser acceptance yo‘q. Next 14.2.5 dependency security upgrade va real lock regeneration hali qolgan.
- Native Mac/Windows, signing, live DB, real LLM va deployment sinovlari hali yo‘q.
- Local revoke allaqachon uchayotgan tashqi HTTP requestni atomik orqaga qaytara olmaydi. No new dispatch best-effort fence va no-blind-retry bor; tashqi side-effect reconciliation talab qilinadi.
- Reauthorization boshlashdan oldin active ulanish explicit revoke qilinadi. Stale token issuance paytida provider grant cleanup’ini mutlaq kafolatlab bo‘lmaydi; audit va operator reconciliation chegarasi saqlangan.
- Google scope nomlari exact canonical set bilan mos bo‘lishi kerak; broad/narrow scope substitutsiyasi avtomatik qilinmaydi.
- OAuth expected_subject onboarding UX avtomatik account discovery emas; operator provider accountni pin qiladi.
- ECC workspace vositalari/skills orasida mavjud emas; o‘rnatildi deb aytilmaydi. Ikki haqiqiy mustaqil source review bajarildi.

## Standart manbalar

Source boundarylari quyidagi hujjatlar bilan solishtirildi: https://developers.google.com/identity/protocols/oauth2/web-server va https://www.rfc-editor.org/rfc/rfc9700.html . Bu protocol guidance, real integration acceptance emas.

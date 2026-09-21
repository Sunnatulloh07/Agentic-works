# PRD-07: Customer 360 va tenant-scope biznes ma'lumotlari

Holat: `IMPLEMENTED_LOCAL`, 2026-09-14. Bu bosqich Customer 360 ma'lumot modelini va xavfsiz lokal API foundationni beradi. Bu hali CRM/ERP provider sync yoki universal connector emas.

## Maqsad

Foydalanuvchi o'z workspace'i ichida mijozni, aloqa ma'lumotlarini, kanal identity'sini va buyurtmalarini bitta tenant-scope ko'rinishda ko'radi. Telefon, email yoki ismning o'zi avtomatik merge uchun yetarli emas.

## Implementatsiya

- `p_customers`: tenant, display name, optional external reference va status.
- `p_customer_contacts`: email, phone, address yoki other; normalize qilingan duplicate himoyasi.
- `p_channel_identities`: channel + external ID tenant ichida unique; explicit `verified=true` bo'lmasa link rad qilinadi.
- `p_customer_orders`: external order ID bo'yicha idempotent upsert.
- Customer detail javobida contacts, channel identities, conversations va orders qaytariladi.
- Barcha querylar tenant va object ID bilan scope qilinadi. Cross-tenant object lookup 404 beradi.
- Search query parametrli va `%`, `_`, `\\` wildcardlari escaped.
- UI'da Customer 360 tab qo'shildi, yangi customer yaratish va detail ko'rish mavjud.

## API

- `GET /platform/{tenant}/customers`
- `POST /platform/{tenant}/customers`
- `GET /platform/{tenant}/customers/{customer_id}`
- `POST /platform/{tenant}/customers/{customer_id}/contacts`
- `POST /platform/{tenant}/customers/{customer_id}/channel-identities`
- `POST /platform/{tenant}/customers/{customer_id}/orders`

Viewer o'qiydi. Owner va operator customer/contact/order mutation qila oladi. Channel identity link explicit verified bo'lishi va keyingi real merge workflow provider/operator dalili bilan kengaytirilishi kerak.

## Acceptance

- 5 ta dependency-free regression test o'tadi.
- Tenant isolation, duplicate channel identity, explicit verification, parametrli search va order idempotency testlangan.
- Bu bosqichda PostgreSQL, CRM API, OAuth, background sync, conflict resolution, soft-delete restore va customer merge UI mavjud emas.

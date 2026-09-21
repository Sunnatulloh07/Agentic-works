# Agent Platform - To'liq qurish master-plan

**Maqsad:** Butun Agent Platform'ni barcha asosiy modullari bilan qurish, yagona tizimga ulash va faqat tizim to'liq tayyor bo'lgandan keyin umumiy acceptance test o'tkazish.

**Asosiy dizayn:** `docs/superpowers/specs/2026-09-13-v1-sales-platform-design.md`

## Ishlash qoidasi

- Oraliq bosqich production release emas.
- Syntax, import va typecheck faqat ichki muhandislik nazorati.
- Asosiy funksional baholash barcha modullar ulangandan keyin.
- Har modulning interface'i oldindan belgilanadi.
- Core mijoz yoki pack nomiga bog'lanmaydi.
- Barcha external write idempotent va audit qilinadi.
- To'liq umumiy test tugamaguncha “platforma tayyor” deyilmaydi.

## 1-bosqich: To'liq kontrakt va domen model

Tenant, role, agent, department, tool, pack, task/run/step, approval, ladder, order, memory, runner/device, audit, usage/billing va API/WebSocket event schema'lari aniqlanadi.

Natija: barcha qatlamlar bir xil nom, ID va state'lar bilan ishlaydi.

## 2-bosqich: Foundation va storage

Settings/config, production validation, SQLite/Postgres interface, migration, transaction, idempotency, backup/restore, Redis limit/cache, correlation ID va structured logging quriladi.

## 3-bosqich: Auth, tenant va security

JWT, owner/operator/integrator/super-admin role'lari, tenant scope, webhook signature, service token, secret rotation, rate limit, request limit, audit principal va isolation guard quriladi.

## 4-bosqich: Pack va agent runtime

Pack manifest, agents, prompts, tools, devices, knowledge, dialect, pack version/signature, agent registry, persona, language, tool policy, ladder va approval policy quriladi.

Pack invalid bo'lsa runtime ishga tushmaydi.

## 5-bosqich: Til, intent va orchestrator

O'zbek lotin/kirill normalization, apostrof variantlari, rus-o'zbek input, dialect dictionary, deterministic intent, Claude gateway, structured intent, agent router, plan/step execution, retry, timeout, cancel, fallback va cost budget quriladi.

LLM to'g'ridan-to'g'ri xavfli action bajarmaydi; yakuniy qarorni policy va approval beradi.

## 6-bosqich: Tool router va cloud integratsiyalar

Typed tool registry, JSON schema validation, MCP boundary, Telegram, Instagram/Meta, Gmail, Google Sheets/Drive/Calendar, CRM, HTTP/webhook va PDF/DOCX/XLSX adapterlari quriladi.

Har adapter timeout, retry, credential isolation, response validation va idempotency bilan ishlaydi.

## 7-bosqich: Approval, ladder va delivery

Approval state machine, xavf klassifikatsiyasi, operator/owner approval, ladder promotion/demotion, transactional outbox, delivery attempt, dead-letter/error state, retry, expiry va external write verification quriladi.

## 8-bosqich: Memory va knowledge base

Document ingest, extraction, chunking, embedding, vector search, tenant collection, agent memory scope, Redis conversation state, factual state, TTL va retention quriladi.

## 9-bosqich: Local Runner

Node/TypeScript runner, WebSocket protocol, device registration, 24 soatlik JWT, heartbeat, atomic task claim/lease, result acknowledgement, reconnect, cancellation, allowlist, deny_always, fs/office/app/print/browser/screen tool'lari, screenshot audit, kill switch, signed update, rollback va offline freeze quriladi.

Runner serverdan task oladi, lekin o'zi agent qarori chiqarmaydi.

## 10-bosqich: UI va operator console

Login/session, role navigation, agent map/detail, task timeline, approval queue, order/business record, delivery status/retry, runner status, audit viewer, reports, tenant settings, layout/theme/language quriladi.

## 11-bosqich: Voice, billing va operation

Speech-to-text, text-to-speech, voice approval, usage accounting, token limit, subscription, billing events, freeze policy, notification, monitoring, alerting va retention jobs quriladi.

## 12-bosqich: Deployment

Docker images, staging/production deployment, secret injection, migration, health/readiness, backup/restore, logs, metrics/traces, rollback va dependency/image scanning tayyorlanadi.

## 13-bosqich: Barcha tizim ulangandan keyingi umumiy test

1. Unit va schema.
2. API contract.
3. Telegram/Instagram real staging.
4. Claude structured tool-calling.
5. Sheets/CRM delivery.
6. Approval/ladder.
7. Memory/RAG tenant isolation.
8. Runner real process.
9. Windows office/printer.
10. UI browser.
11. Docker end-to-end.
12. Duplicate/concurrency.
13. Timeout/retry/recovery.
14. Backup/restore/migration.
15. Security audit.
16. Load test.
17. Uch kunlik real pilot.

## 14-bosqich: Final release gate

Release faqat kritik security/data-loss muammosi qolmaganda, barcha testlar yashil bo'lganda, external staging dalili mavjud bo'lganda, tenant isolation va runner lease tasdiqlanganda, backup/restore tekshirilganda va operator acceptance checklist imzolanganda beriladi.

## Qat'iy taqiqlar

- LLM kalitini kodga yozish.
- Pack mazmunini core kodga qotirish.
- Mock testni real integration deb ko'rsatish.
- CSV'ni Google Sheets deb sotish.
- WebSocket testini haqiqiy runner testi deb aytish.
- Approval'siz destructive yoki physical action.
- Umumiy testdan oldin production release.

# Eski `api-python/tests/` nafaqaga chiqarildi — 2026-09-22

Sabab: 205 testdan 33 tasi qizil, CI gate uni ishga tushirmaydi
(`.github/workflows/verify.yml` faqat `integration_tests`), va atigi 5 fayl tirik kodni sinardi.

- Route'lari 410 Gone'ga chiqarilgani uchun o'chirildi (`app.main` import qiladi): `test_agents`,
  `test_audit_fixes`, `test_authz`, `test_gaps`, `test_health`, `test_operator`, `test_stage1`,
  `test_stage2`, `test_stage4`, `test_stage6`, `test_stage8`, `test_tenant_auth`.
- Platform rejimida o'lik modullar uchun o'chirildi: `test_orchestrator` va `test_tool_registry`
  (orchestrator, tool_registry), `test_ports` (app.ports), `test_domain` va
  `test_storage_foundation` (storage domain deliveries), `test_telegram_api`
  (integrations.telegram_api), `test_redis` (redis:6380 o'chiq — doim SKIP).
- Saqlanib shu papkaga ko'chirildi (pydantic/yaml kerak, `runtime_tests`'da yashay olmaydi):
  `test_packs.py` → `test_packs_contract.py`, `test_pack_persona.py`, `test_lang.py`,
  `test_config.py`, `test_security.py` → `test_security_helpers.py`.
- `tests/conftest.py` ham o'chdi; uning `_isolated_stores` o'rniga `test_security_helpers.py`
  ichida minimal `APP_DB` → `tmp_path` izolyatsiyasi qoldirildi.

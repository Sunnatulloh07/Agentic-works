# Lokal verification muhiti: Windows portability tuzatishlari

Sana: 2026-09-19. Sessiya turi: **verification-tooling tuzatish**, product runtime o‘zgarmadi.
Production qarori o‘zgarmaydi: **NO_GO**.

## Nima uchun bu blok kerak bo‘ldi

v0.3.8 dalillari (`docs/verification/development-v038-final/summary.json`) faqat Linux muhitida
olingan: `/opt/venv/bin/python` (3.11) + `cryptography` + `bun` + `/usr/local/bin/node`. Ishchi
Windows mashinada `python scripts/verify_offline.py` **bir xil natijani bermadi**:

| Holat | Daraxtdagi son | Windows’da haqiqiy son |
|---|---:|---:|
| Python runtime testlari | 835 PASS | 287 topildi, 1 FAIL, 79 ERROR |
| Node/browser suite (4 ta) | 64 PASS | 4 ta suite ham yiqildi |
| JS/TS syntax | PASS | BLOCKED (bun yo‘q) |

Ya’ni “908 test PASS” da’vosi lokal mashinada qayta tiklanmasdi. Sabab product kodida emas,
**verification harness va testlarning platformaga bog‘liqligida** edi.

## Topilgan ildiz sabablar va tuzatishlar

### 1. Harness child environment’dan Windows uchun zarur o‘zgaruvchilarni olib tashlagan

`verify_offline.py` child env’ni noldan qurardi va faqat `PATH/HOME/LANG/PYTHONPATH` berardi.
Windows’da `SystemRoot` bo‘lmasa Winsock provider ishga tushmaydi:

```
File "C:\Python314\Lib\asyncio\windows_events.py", line 8, in <module>
    import _overlapped
OSError: [WinError 10106] ...
```

`unittest.mock` → `asyncio` → `_overlapped` zanjiri sababli **79 test moduli umuman import
bo‘lmadi**. Node ham xuddi shu sababdan CSPRNG initda abort qilardi
(`Assertion failed: ncrypto::CSPRNG(nullptr, 0)`) va 4 suite ham yiqilardi.

Tuzatish: `WINDOWS_PASSTHROUGH` ro‘yxati (SystemRoot, SystemDrive, windir, COMSPEC, PATHEXT,
NUMBER_OF_PROCESSORS, PROCESSOR_ARCHITECTURE, OS) qo‘shildi. Bu qiymatlar credential, proxy yoki
provider konfiguratsiyasi emas, shuning uchun izolyatsiya siyosati saqlanadi.

### 2. Child’da `TEMP` yo‘q edi — testlar source daraxtiga chiqindi yozgan

Windows’da `tempfile` TMPDIR/TEMP/TMP bo‘lmasa `os.getcwd()`ga tushadi. Natijada testlar
`api-python/` ichida scratch baza yaratgan:

```
api-python/tmpm6iv9g2d/upgrade.db
api-python/tmpodqr7f57/upgrade.db
api-python/tmptwrbvopx/budget.db
api-python/tmpz4uk3sua/
```

Bu `verify_manifest.py` uchun “Unlisted file” bo‘ladi, ya’ni provenance gate’ni ifloslantiradi.
Tuzatish: `TEMP/TMP/TMPDIR` va `USERPROFILE` harness’ning o‘z throwaway katalogiga o‘rnatildi.
Yangi yurishlarda `api-python/tmp*` paydo bo‘lmaydi.

### 3. Har bir job uchun 90 soniyalik qattiq timeout to‘liq suite’ga yetmasdi

835 test Linux’da ~27 s, Windows’da bir necha barobar sekin. Natijada log
`Verification command timed out` bilan **kesilardi** va test soni dalilda ko‘rinmasdi.
Tuzatish: `JOB_TIMEOUT_SECONDS = 90`, `JOB_TIMEOUT_OVERRIDES = {'python_runtime': 600}`.
Deadlock’ka qarshi chegara saqlanadi, lekin sekin platformada dalil to‘liq yig‘iladi.

### 4. `scripts/test_manifest.py` symlink testi Windows’da ERROR bergan

Windows symlink uchun `SeCreateSymbolicLinkPrivilege` talab qiladi → `OSError [WinError 1314]`.
Test endi `skipTest` qiladi. **POSIX’da tekshiruv o‘zgarmagan** (symlink rad etilishi
haqiqatan sinaladi), Windows’da esa ko‘rinadigan `skipped` bo‘ladi.

### 5. `scripts/test_release_tools.py` POSIX mode bitini Windows’da talab qilgan

`assertEqual(0o600, st_mode & 0o777)` Windows’da 0o666 beradi, chunki chmod faqat read-only
flagini boshqaradi. Tekshiruv `os.name == 'posix'` bilan o‘raldi; POSIX’da o‘zgarmagan.

### 6. `integration_tests/test_cors_and_retirement_http.py` FastAPI versiyasiga bog‘liq edi

FastAPI 0.141 / Starlette 1.6 `include_router` natijasini `_IncludedRouter` wrapper sifatida
saqlaydi va unda `.path` yo‘q:

```
AttributeError: '_IncludedRouter' object has no attribute 'path'
```

Test endi `original_router` orqali rekursiv yig‘adi. **Xavfsizlik da’vosi aynan saqlanadi**:
tekshiruvdan keyin 75 route path topiladi, `/runner/ws` yo‘q, `/platform/runner/ws` bor.

### 7. Default codec — UTF-8 o‘zbek matnli fayllar Windows’da o‘qilmagan

Windows’da `Path.read_text()` va `open()` platforma kodlashini (cp1251) ishlatadi. Source va
fixture’larda `o‘/g‘/—` bor, shuning uchun 8 test `UnicodeDecodeError` bilan yiqilgan:

```
UnicodeDecodeError: 'charmap' codec can't decode byte 0x98 in position 3205
```

Bu **product bug'i emas, o‘qish yo‘li bug’i**: JSON/source fayllar UTF-8 deb yoziladi, lekin
o‘qishda explicit kodlash berilmagan. Tuzatilgan joylar:

| Fayl | Nima o‘qiladi |
|---|---|
| `api-python/runtime_tests/test_security_surface_v037.py` | `app/main.py` (AST parse) |
| `api-python/runtime_tests/test_v037_migration.py` | `fixtures/v036_schema.sql` |
| `scripts/check_release.py` | evidence JSON |

`check_release.py` alohida muhim: release gate evidence fayli aynan o‘zbek tilidagi sabablarni
saqlaydi, ya’ni haqiqiy release’da bu **har doim** yiqilardi. Tekshirildi: UTF-8 evidence
`{"note": "o‘zbek tili — tasdiq"}` endi xatosiz o‘qiladi va to‘g‘ri `NO_GO` beradi.
`api-python/runtime_tests/test_planning.py`da bu allaqachon to‘g‘ri yozilgan — endi qolgan
joylar ham mos.

### 8. `test_v037_migration.py` Windows’da faylni qulflab qolgan

`with sqlite3.connect(path) as db:` — sqlite3 context manager **commit qiladi, lekin
yopmaydi**. Windows ochiq handle bilan faylni qulflaydi, shuning uchun keyingi `Engine(path,...)`
yiqilardi va bu **har safar** takrorlanardi (3/3 yurish):

```
PermissionError: [WinError 32] ... 'upgrade.db'
```

Linux’da ochiq faylni o‘chirish mumkin, shuning uchun u yerda sezilmagan. Suite’ning qolgan
qismi `closing(...)` ishlatadi; endi bu test ham shunday (`from contextlib import closing`,
`with closing(sqlite3.connect(path)) as db, db:`). Ikki ketma-ket yurishda OK.

## O‘lchangan natija (tuzatishdan keyin)

`python scripts/verify_offline.py` yurishi (Windows 11, Python 3.14.6, Node 24.18.1):

| Guruh | Oldin | Keyin |
|---|---|---|
| python_runtime | 287 topildi, 79 import ERROR | **919 topildi, import ERROR 0** |
| release_tools | FAIL (POSIX mode) | PASS (3) |
| manifest_tools | FAIL (symlink) | PASS (6) |
| sqlite_demo | PASS | PASS |
| managed_database_demo | FAIL (WinError 10106) | PASS |
| node_runner | FAIL (CSPRNG abort) | FAIL — POSIX ownership kontrakti |
| browser_session_client | FAIL | PASS |
| browser_oauth_client | FAIL | PASS |
| browser_google_data_client | FAIL | PASS |
| javascript_typescript_syntax | BLOCKED | BLOCKED (bun yo‘q) |
| python_syntax | PASS | PASS |

`python_runtime`dagi qolgan **150 error + 1 failure** to‘liq taqsimoti:

| Soni | Sabab | Tabiati |
|---:|---|---|
| 138 | `VaultError` | `cryptography` o‘rnatilmagan — muhit |
| 6 | `os.O_NOFOLLOW` yo‘q | POSIX-only runner/portable-fs |
| 2 | `os.mkfifo` yo‘q | POSIX-only |
| 2 | WinError 1314 symlink huquqi | Windows privilege |
| 1 | `fcntl` yo‘q | POSIX/darwin-only |
| 1 | `test_all_files_private` `st_mode & 0o077` | POSIX mode bit |
| 1 | `test_mount_scope` `/var` mount root | POSIX yo‘l |

Ya’ni **kod nuqsoni qolmadi**: qolgan hammasi POSIX-only xavfsizlik kontrakti yoki yetishmayotgan
dependency. `cryptography` o‘rnatilsa 138 tadan katta qismi yopiladi — bu **o‘lchandi**
(keyingi bo‘limga qarang: `errors=149 → 11`).

### O‘lchangan tasdiq (2026-09-20)

`cryptography` boshqariladigan venv’ga o‘rnatildi va to‘plam qayta yurildi. Taxmin
**o‘lchangan faktga** aylandi:

| Holat | `Ran` | `failures` | `errors` | `skipped` |
|---|---:|---:|---:|---:|
| `cryptography` **yo‘q** | 2411 | 1 | **149** | 1 |
| `cryptography` **bor** | 2411 | 1 | **11** | 1 |

Ya’ni **138 error yopildi** — ular **chindan ham muhit** edi, kod emas. Qolgan **11** tasi
butunlay POSIX-only va Windows’da **hech qachon** yurmaydi:

| Soni | Test | Sabab |
|---:|---|---|
| 6 | `test_portable_fs` | `os.O_NOFOLLOW` yo‘q |
| 2 | `test_portable_fs` | `os.mkfifo` yo‘q |
| 1 | `test_portable_fs` | `fcntl` yo‘q (darwin shartnomasi) |
| 1 | `test_foundation_v02.test_mount_scope` | `/var` mount root |
| 1 | `test_macos_bundle.test_all_files_private` | `st_mode & 0o077` mode bit |

Bu **kod nuqsoni emas** va tuzatilmaydi: POSIX xavfsizlik kontraktlari (`O_NOFOLLOW`,
`mkfifo`, fayl mode bitlari) Windows’da mavjud emas. Ular Linux/macOS CI’da yurishi kerak.

Yurgizish (boshqariladigan venv bilan):

```sh
cd api-python
PY="$HOME/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
PYTHONPATH=".;$(python -c 'import site;print(site.getsitepackages()[0])')" \
  "$PY" -m unittest discover -s runtime_tests -t runtime_tests
```



## HTTP integration suite — avval yiqilgan, endi **to‘liq yashil**

`api-python/integration_tests/` hozirgacha **NOT_RUN** edi. Birinchi yurishda fastapi,
pydantic, pyjwt, pyyaml, pytest, httpx, uvicorn mavjud bo‘lgan muhitda:

```sh
cd api-python
ENV=test ALLOW_INSECURE_DEV=true PIPELINE_MODE=platform python -m pytest integration_tests -q
```

Natija (birinchi yurish): `5 failed, 72 passed, 11 errors in 36.86s`.

| Guruh | Sabab | Holat |
|---|---|---|
| `test_oauth_http.py` 4 test | `cryptography` yo‘q → `SecretVault` `503` | **Yopildi** |
| `test_google_data_http.py` 11 error | Aynan shu `SecretVault` `seal()` xatosi | **Yopildi** |
| `test_cors_and_retirement_http.py` 1 test | FastAPI `_IncludedRouter` | **Tuzatildi** |

### Qayta o‘lchov (2026-09-20) — **hammasi yashil**

`cryptography` boshqariladigan venv’ga o‘rnatildi va to‘plam qayta yurildi:

```sh
cd api-python
PY="$HOME/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe"
ENV=test ALLOW_INSECURE_DEV=true PIPELINE_MODE=platform \
  PYTHONPATH=".;$(python -c 'import site;print(site.getsitepackages()[0])')" \
  "$PY" -m pytest integration_tests -q
```

| Yurish | Natija |
|---|---|
| Birinchi (`cryptography` yo‘q) | `5 failed, 72 passed, 11 errors` |
| **Qayta (`cryptography` bor)** | **`95 passed`** |

Ya’ni **15 failure + 11 error yopildi** va to‘plam **95/95 yashil**. Bu — `auth`/`oauth`/
`google_data` HTTP yuzalarining **birinchi haqiqiy to‘liq tasdig‘i**. Oldingi “15–16 failure
yopilishi kutiladi” — **taxmin** edi; endi **o‘lchangan fakt**.

`runtime_tests`da ham xuddi shu o‘rnatish `errors=149 → 11` berdi (yuqoridagi tasdiq jadvali).

## Ochiq qolgan platforma cheklovlari (yashirilmaydi)

1. **Tizim Python’ida deps yo‘q** → hamma narsa boshqariladigan venv bilan yuriladi
   (`~/.workbuddy-ai/binaries/python/envs/default`, yuqoridagi buyruqlar). O‘lchandi:
   `runtime_tests` `errors=149 → 11`, `integration_tests` `95 passed`.
2. **`bun` yo‘q** → JS/TS syntax guruhi BLOCKED (typecheck/build emas, u baribir alohida gate).
3. **POSIX-only xavfsizlik testlari.** `apps/runner/test.js` “private single-owner file/dir”
   (0600 + owner uid) ni talab qiladi; Windows’da bu semantika yo‘q, shuning uchun runner suite
   Windows’da yiqiladi. `runtime_tests/test_portable_fs.py` symlink/ownership testlari ham
   shunday. Bular **ataylab** POSIX kontrakti (README: filesystem ijrosi faqat Linux), shuning
   uchun ularni “yashil” qilish uchun kodni bo‘shatish noto‘g‘ri bo‘lardi.
4. **Versiya farqi.** Lokal: Python 3.14.6, Node 24.18.1, FastAPI 0.141.1, Starlette 1.6.0.
   CI: Python 3.11, Node 22. Bu farq aynan 6-banddagi bug’ni ochdi; CI ham yangi FastAPI bilan
   yurilsa xuddi shu joyda yiqiladi.
5. **`MANIFEST.sha256` joriy daraxtga mos emas.** Hozir `verify_manifest.py` FAIL:
   12 fayl hash’i mos emas (v0.3.8 paytida o‘zgargan), ko‘plab yangi fayl (CRM bloki, yangi
   testlar) ro‘yxatda yo‘q. Repo ichida manifest generatori **yo‘q** — u ZIP tayyorlashda
   tashqarida yasaladi. Ya’ni bu gate development daraxtida hech qachon yashil bo‘lmaydi.
6. **CI matritsasi bitta OS.** `.github/workflows/verify.yml` faqat `ubuntu-latest`da yuradi.
   Yuqoridagi 8 ta nuqsondan 7 tasi faqat Windows’da ko‘rinadi, ya’ni Windows ishlab chiqish
   muhiti uchun hech qanday CI qo‘riqchisi yo‘q. Windows matritsasi qo‘shilishi tavsiya etiladi;
   u holda POSIX-only guruhlar platforma shartiga ko‘ra ajratilishi kerak (skip, fail emas).

## Qayta tiklash buyruqlari

```sh
# Offline guruhlar (Linux/Windows)
python scripts/verify_offline.py --output /tmp/verification-local

# HTTP gate (dependency muhitida)
cd api-python
ENV=test ALLOW_INSECURE_DEV=true PIPELINE_MODE=platform python -m pytest integration_tests -q
```

## Bu hujjat da’vo qilmaydigan narsalar

Bu o‘zgarishlar product runtime’iga, pack kontraktiga yoki security siyosatiga ta’sir qilmaydi.
`cryptography`/`bun` o‘rnatilmagan, live provider, Postgres, React typecheck/build, browser E2E,
staging pilot va deployment bajarilmagan. Har bir guruh o‘z logini `summary.json`da ko‘rsatadi;
yashil bo‘lmagan guruhni PASS deb hisoblash mumkin emas.

## Joriy o'lchangan to'plam (2026-09-20, fazza 26 dan keyin)

Auditning oxirgi ikki fazasi (`erp.py` chegaralari va ifodalab bo'lmaydigan
sonlar) to'plamga **18** sinov qo'shdi. Imzo **o'zgarmadi**:

| Ko'rsatkich | Qiymat |
|---|---|
| `Ran` | **2784** (2800 - 20 + 4; pastga qarang) |
| `failures` | **1** — `test_macos_bundle.test_all_files_private` (POSIX mode bit) |
| `errors` | **11** — hammasi POSIX-only, yuqoridagi jadval bilan bir xil |
| `skipped` | **1** — Windows symlink huquqi |

Probe (`scripts/probe_truncation_verdict.py`) ham o'sdi: **334 → 1683** xossa,
**1683/1683** yashil. Bu ikki faza o'zgargan fayllar:

| Fayl | Nima o'zgardi |
|---|---|
| `platform_runtime/erp.py` | `_date_text` rad etish shoxi + docstring |
| `platform_runtime/inventory.py` | `float()` guard; `MAX_QUANTITY` olib tashlandi |
| `platform_runtime/oee.py` | `float()` guard |
| `platform_runtime/manufacturing.py` | `float()` guard |
| `platform_runtime/workforce.py` | `float()` guard |
| `platform_runtime/business_graph.py` | `canonical` matn shakliga tushadi |
| `runtime_tests/test_erp.py` | 56 → 68 |
| `runtime_tests/test_inventory.py` | 82 → 84 |
| `runtime_tests/test_oee.py` | 73 → 74 |
| `runtime_tests/test_manufacturing.py` | 70 → 71 |
| `runtime_tests/test_workforce.py` | 36 → 37 |
| `runtime_tests/test_business_graph.py` | 174 → 175 |
| `platform_runtime/database/contract.py` | versiya shifti + `_utf8_size` |
| `runtime_tests/test_managed_database.py` | 48 → 52 |
| `platform_runtime/agent_loop.py` | faol holat to‘plami bitta bayonotga |
| `runtime_tests/test_agent_loop.py` | 30 → 39 |
| `platform_runtime/usage_budget.py` | `bounded` qoidalari `contract.text` bilan bir xil |
| `platform_runtime/tools.py` | obyekt chegarasi bayt bilan o‘lchanadi |
| `runtime_tests/test_usage_budget.py` | 36 → 46 |
| `runtime_tests/test_adapters.py` | 16 → 18 |
| `platform_runtime/whatsapp_inbound.py` | oyna konstantasi bog‘lanishi hujjatlandi |
| `runtime_tests/test_whatsapp.py` | 120 → 127 |
| `runtime_tests/test_whatsapp_inbound.py` | 70 → 74 |
| `platform_runtime/telephony.py` | uchta o‘lik konstanta olib tashlandi |
| `platform_runtime/vision.py` | `MAX_WINDOW_DAYS` olib tashlandi |
| `runtime_tests/test_telephony.py` | 139 → 147 |
| `runtime_tests/test_vision.py` | 49 → 50 |
| `platform_runtime/connectors.py` | ulanish nomi darvozasi `_string` bilan bir xil |
| `runtime_tests/test_connector_authority.py` | 37 → 47 |
| `runtime_tests/test_connector_contract.py` | 4 → 8 |
| `platform_runtime/knowledge.py` | embedding qo‘riqchisi tartibi |
| `runtime_tests/test_knowledge.py` | 34 → 45 |
| `platform_runtime/vision.py` | `truncated` qaytarilgan narsaga nisbatan |
| `runtime_tests/test_vision.py` | 50 → 58 |
| `platform_runtime/assets.py` | `tree` endi kesilmagan deb xabar beradi |
| `runtime_tests/test_assets.py` | 59 → 65 |
| `platform_runtime/sheets.py` | o‘qish kesimi sarlavhani hisobga oladi; `max_rows` kesiladi |
| `runtime_tests/test_sheets.py` | 29 → 66 |
| `platform_runtime/documents.py` | `bank_account` raqamlari; kalendar sanasi; butun-sonli mediana |
| `runtime_tests/test_documents.py` | 100 → 117 |
| `crm/crm_gateway.py` | `allowed_connections` **qat'iy** o'qiladi |
| `crm/crm_contract.py` | `bounded_int`; narx/sana/davomiylik chegaralari |
| `runtime_tests/test_crm_contract.py` | 13 → 33 |
| `runtime_tests/test_crm_gateway.py` | 9 → 23 |
| `crm/bitrix24_adapter.py` · `crm/kommo_adapter.py` | `timeout_seconds` o‘qiladi; narx `provider_price` orqali |
| `crm/onec_adapter.py` · `crm/custom_http_adapter.py` | provider maydoni endi **yiqitmaydi** |
| `runtime_tests/test_crm_adapter_boundaries.py` | yangi, 13 test |
| `platform_runtime/reengagement.py` | `_identifier` bo‘shliqni rad etadi |
| `runtime_tests/test_reengagement.py` | 35 → 84 |
| `platform_runtime/supervisor.py` | `request_key` shifti run kalitiga bog‘landi; `MAX_SECTIONS` qo‘llanadi |
| `platform_runtime/oversight.py` | `MAX_RUNS` olib tashlandi; `VIEWS` qo‘riqchi; `_text` kesadi |
| `runtime_tests/test_supervisor.py` | 60 → 131 |
| `runtime_tests/test_oversight.py` | 38 → 85 |

**Eslatma:** `erp.py`, `inventory.py`, `manufacturing.py`, `business_graph.py` —
**CRLF**; `oee.py`, `workforce.py` — **LF**; `runtime_tests/test_business_graph.py`
— **LF**, qolgan test fayllari **CRLF**; `ULTRA-AUDIT-ASCII-CELL-UZ.md` — **LF**.
Faylni Python bilan qayta yozish satr oxirini o'zgartiradi va diff butun faylni
"o'zgargan" deb ko'rsatadi — bayt sifatida yozing (`read_bytes`/`write_bytes`).

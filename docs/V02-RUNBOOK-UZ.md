# v0.2 lokal tekshirish va konfiguratsiya

Bu qo‘llanma engineering preview uchun. Next dependency security upgrade va release gate’lar yopilmaguncha internetga production sifatida chiqarmang.

## Birinchi tekshiruv

Original ZIPni saqlang. Yangi ZIPni alohida katalogga oching. Python 3.11+ va Node 22.4+ kerak. Computer’da biz runtime unit va runner testlarini bajardik; sizning muhitda dependency-backed tekshiruvlar qo‘shimcha bajariladi.

````bash
cd api-python
python -m unittest discover -s runtime_tests -v
cd ..
node --test apps/runner/test.js
python scripts/demo_runtime.py
````

Demo providerga chiqmaydi, lokal temporary DB ishlatadi. Kutiladigan holat: before approval waiting_approval, after approval succeeded, bitta persisted record, replay o‘sha task.

## Auth va local setup

`python scripts/setup_local.py` .env yo‘q bo‘lsa unique JWT/admin/webhook secretlar yaratadi, qiymatlarni stdoutga chiqarmaydi. Keyin review qilingan Python deps bilan API/worker ishga tushiriladi. `scripts/owner_login.py` lokal `/auth/token` orqali tokenni faylga saqlaydi. Tokenni chatga yoki gitga yubormang. Hozir haqiqiy signup yoki password login yo‘q.

`ENV=dev` avtomatik insecure mode emas. `ALLOW_INSECURE_DEV=true`ni faqat izolyatsiyalangan testda ishlating. Productionda env flag o‘zgarishi bilan normal secrets talabi chetlab o‘tilmaydi.

## SQLite export connector

Mijoz bazasining minimal, PII tozalangan exportini read-only mount qiling. `config/customer-sqlite.example.json`dagi `connections`ni amaldagi tenant entry’ga merge qiling; butun integrations.jsonni almashtirmang. Environment misoli:

````text
PLATFORM_DB_ROOTS=["/srv/customer-db"]
PLATFORM_INTEGRATIONS_FILE=/srv/config/integrations.json
````

Bu mountni API va workerning ikkalasiga ko‘rinarli qiling. Volume read-only bo‘lsin. Rootni `/` yoki platformaning data katalogiga qo‘ymang. `tables`dagi ustunlar exact allowlist, password yoki maxfiy fieldlarni qo‘shmang.

Pack `agents`ga kerakli `data.crm_reader`ni `config/agent-capabilities.example.yaml`dan ko‘chiring. JSON task steps:

````json
[{"tool":"connectors.read","args":{"connection":"crm-export","table":"contacts","columns":["id","name"],"limit":20}}]
````

Bu native CRM write emas. Agent va connection scope serverda tekshiriladi. Metadata `GET /platform/{tenant}/connections`, owner/integrator roli. Configured status live provider verified degani emas.

## Aisha TTS

Aisha API billing va key muvofiqligini avval provider bilan tasdiqlang. Server secret store’dan environment credential bering, configda faqat reference saqlansin:

````json
{"speech":{"aisha":{"key_env":"DEMO_AISHA_API_KEY"}}}
````

Ovoz agentini packga explicit qo‘shing. Pullik call qilishdan oldin operator/owner tasdiq beradi. `independent:true` bo‘lsa creator va approver alohida actor bo‘ladi.

````json
[{"tool":"voice.tts","args":{"text":"Assalomu alaykum, sizga qanday yordam bera olaman?","mood":"Neutral"}}]
````

Task `audio_path` metadata qaytaradi, audio file playback/download implement qilinmagan. `download_verified:false` aniq ko‘rsatiladi. Real key bilan tasdiqlab worker ishlatilsa haqiqiy provider xarajati yuz beradi. Bu paketni tayyorlashda bunday call bajarilmadi.

STT adapter metodini integratsiya qilishdan oldin tenant upload, consent, content validation, retention, budget va natija storage lifecycle qurilsin. 1 MB local limit, rasmiy Aisha v1 maksimumi emas. Stream call/meeting uchun alohida gateway zarur.

## MCP xavfsizlik o‘zgarishi

Faqat `allowed_tools` endi yetmaydi. Har nom uchun exact `tool_schemas` bo‘lsin. Namuna sxemani remote providerning real kontraktiga moslashtiring. Unknown field reject qilinadi. Eski config bilan request tarmoqqacha rad etilishi ataylab qilingan breaking change.

## CI va production oldi

Paket o‘rnatish mumkin bo‘lgan CI’da Python HTTP suite, React typecheck va Next buildni alohida bajaring. Versionlarni lock/constraints bilan audit qiling. Eski Next14.2.5ni aynan productionga install/deploy qilish tavsiya etilmaydi. Supported upgrade branchda lockfile clean regenerate qilinsin.

`docs/verification/v02-summary.json` lokal tekshirilgan va bloklangan qismlarni ajratadi. Internet secretlari, production DB yoki real mijoz audiosini chat orqali yuborish kerak emas.

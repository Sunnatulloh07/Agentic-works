# Favqulodda tiklash (DR) runbook

> Bu hujjat **operatsion** qo‘llanma. U `scripts/dr_drill.py` bilan o‘lchangan
> qismni va **o‘lchanmagan** qismni ajratib ko‘rsatadi. Yashil drill — bu tayyorlik
> dalili emas, balki uning **bir qismi**.

## 0. Drill nima isbotlaydi (o‘lchandi)

```
python scripts/dr_drill.py --report docs/verification/dr-drill.json
```

Oxirgi o‘lchov (Windows, 2026-09-21): **42 jadval, 16 satr, 0.295 s**, hamma qadam `ok`.

| # | Qadam | Nima isbotlanadi |
|---|---|---|
| 1 | `seed through Engine` | Sxema **haqiqiy migratsiyalar** orqali yaratiladi, qo‘lda yozilgan DDL emas |
| 2 | `snapshot` | Ochiq baza ustidan nusxa olish mumkin (`sqlite3` backup API) |
| 3 | `snapshot integrity` | `PRAGMA integrity_check == ok` |
| 4 | `restore from snapshot` | Nusxa **o‘zi ham manba** — tiklash alohida, kam sinalgan kod yo‘li emas |
| 5 | `row counts match` | Har bir user jadval satr soni aynan teng |
| 6 | `tenant isolation` | Tiklangan nusxada tenant filtri **hamon** ajratadi |
| 7 | `engine opens restored copy` | Tiklangan fayl `Engine` bilan ochiladi va o‘qiydi |

6-qadam eng muhimi va u **ataylab** shunday: tenantlarni jimgina birlashtirib
yuborgan tiklash — muvaffaqiyatsiz tiklashdan **yomonroq**, chunki u muvaffaqiyat
kabi ko‘rinadi.

## 1. Drill nima isbotlamaydi (o‘lchanmagan)

Bular `report['not_covered']` da ham qaytariladi:

- **Shifrlangan artefakt** — nusxa shifrlanmagan holda yoziladi (`0600`).
- **Masofadagi nusxa** — offsite/remote saqlash yo‘q.
- **Retention siyosati** — qancha saqlash, qachon o‘chirish belgilanmagan.
- **Ishchi jarayon ishlab turganda tiklash** — worker/API to‘xtatilishi talab qilinadi.
- **Point-in-time recovery** — WAL arxivi yo‘q.
- **Tarmoq bazalari** (PostgreSQL, MySQL) — drill faqat SQLite uchun.
- **HA / klaster** — bitta instance uchun yozilgan; failover mashqi yo‘q.

## 2. Haqiqiy bazani mashq qilish

```bash
# 1. Worker va API ni to'xtating (SQLite uchun majburiy)
# 2. Nusxa oling
python scripts/sqlite_backup.py /path/platform.db /backup/platform-$(date +%F).db

# 3. Nusxani tekshiring — manba sifatida ishlatib
python scripts/dr_drill.py --source /backup/platform-$(date +%F).db --keep /tmp/dr

# 4. Tiklangandan keyin Engine ochilishini tasdiqlang (drill buni o'zi qiladi)
```

`--source` bilan drill **haqiqiy** bazani o‘qiydi (faqat o‘qish), lekin uni
o‘zgartirmaydi va hech narsa chop etmaydi.

## 3. Tiklash tartibi

1. **Yozishni to‘xtating.** SQLite uchun: API va worker jarayonlarini to‘xtatish.
2. **Nusxani tasdiqlang.** `python scripts/dr_drill.py --source <nusxa>`.
   Yashil bo‘lmasa — keyingi qadamga **o‘tmang**.
3. **Eski faylni almashtiring, o‘chirmang.** Nomini o‘zgartirib saqlang.
4. **Tiklang.** `python scripts/sqlite_backup.py <nusxa> <platform.db>`.
5. **Tekshiring.** `PRAGMA integrity_check`, keyin `/health` va `/platform/{tenant}/tasks`.
6. **Tenant izolyatsiyasini ko‘z bilan ko‘ring.** Bitta tenant bilan kiring va
   boshqa tenant vazifalari **ko‘rinmasligini** tasdiqlang.
7. **Audit jurnalini tekshiring.** Tiklashdan keyingi yozuvlar uzluksiz bo‘lishi kerak.
8. **Nima yo‘qolganini yozib qo‘ying.** Nusxa olingan vaqt va tiklash vaqti orasidagi
   yozuvlar yo‘qoladi. Bu **qabul qilinadigan yo‘qotish** (RPO) — uni da’vo qilmang,
   o‘lchang.

## 4. RTO / RPO — hozirgi holat

| Ko‘rsatkich | Qiymat | Manba |
|---|---|---|
| Drill davomiyligi (sintetik) | **0.295 s** | `docs/verification/dr-drill.json` |
| RTO (haqiqiy baza) | **o‘lchanmagan** | Hajmga bog‘liq |
| RPO | **o‘lchanmagan** | WAL arxivi yo‘q |

Bu jadval ataylab bo‘sh. RTO va RPO ni **haqiqiy** baza hajmi bilan o‘lchash kerak;
sintetik drill 16 satr bilan ishlaydi va u hech qanday tiklash vaqti haqida gapira
olmaydi.

## 5. Nima hali qurilmagan

- Avtomatik davriy nusxa (cron/systemd timer) — **yo‘q**
- Nusxani shifrlash — **yo‘q**
- Offsite ko‘chirish — **yo‘q**
- Tiklashni avtomatik tekshirish (drillni CI’da yuritish) — **taklif**
- Ko‘p-instansiyali HA va failover — **yo‘q**

Shuning uchun `production_release: NO_GO` **o‘zgarmaydi**: drill bir savolga javob
berdi («nusxa tiklanadimi?»), qolganlariga emas.

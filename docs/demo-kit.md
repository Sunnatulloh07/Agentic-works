# Demo kit — birinchi sotuv uchun (docs §6 row 7)

## 3 daqiqalik demo ssenariy (video)
1. (0:00) Mijoz Telegram'da: `KB010 narxi?` → bot: narx + o'lcham (5 sek)
2. (0:30) Mijoz: `/buy KB010 1 chilonzor +998901112233 Laylo` → bot: "operator tasdiqlaydi" (5 sek)
3. (1:00) Operator UI: tasdiq navbatida yangi qator → **Tasdiqlash** (10 sek)
4. (1:30) Hozirgi kodda CSV outbox: yangi qator (buyurtma ID bilan) (10 sek). Google Sheets hali ulanmagan.
5. (2:00) Statistika: bugungi 1 buyurtma panelda (10 sek)
6. (2:30) Ovozli xulosa: "30 soniyada javob, 2 daqiqada buyurtma, xodim faqat tasdiqlaydi"

## 10 ta do'kon suhbati (5 daqiqa, 10 savol)
1. Kuniga nechta odam Telegram/Instagram'da narx so'raydi?
2. Javob berishga ulgurmay qolgan kun bo'ladimi? Haftada necha marta?
3. Eng ko'p takrorlanadigan 3 savol nima?
4. Buyurtmani hozir qanday yozasiz (daftar/Excel/xotira)?
5. Necha filial? Ular orasida buyurtma adashganmi?
6. Kechasi yozgan mijoz ertalabgacha kutadimi yoki ketadimi?
7. Xodimlar soni? Kim javob beradi (shu odam ketsa nima bo'ladi)?
8. Qaytarish/norozi mijoz haftada nechta?
9. Oylik shunga o'xshash muammoga qancha pul ketadi (vaqt + yo'qolgan mijoz)?
10. Agar bot 30 soniyada javob bersa, sinab ko'rasizmi? (1 hafta bepul pilot)

## Pilot qabul mezoni (pul shundan keyin)
- [ ] Hozirgi ichki testlar yashil (`146 passed, 4 skipped`); bu real Telegram yoki Sheets integratsiyasi isboti emas.
- [ ] To'liq mahsulot uchun real Telegram + Sheets staging testi o'tgan
- [ ] Jonli 3 kun: o'rtacha javob ≤ 60 sek, 0 yo'qolgan buyurtma
- [ ] Xodim kuniga ≤ 15 daqiqa tasdiqqa sarflaydi
- [ ] 1 ta real buyurtma bot orqali tushdi
- Narx: setup $300 (birinchi mijoz) + $150/oy. Keyingilari: $800 + $200/oy.

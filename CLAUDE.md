# Agent Platform — vibe coding qoidalari (JARVIS PDF + PRD'dan)

## Xavfsizlik (birinchi o'rinda)
- Avval o'qi, keyin o'zgartir. Birinchi vazifa har doim read-only audit.
- Tahrirdan oldin faylni to'liq o'qi. Funksiyani o'zgartirishdan oldin uning barcha
  chaqiruvchilarini grep qil. O'qish:tahrir nisbati kamida 4:1 bo'lsin — o'qimasdan
  tahrirlash retry va behuda token keltiradi (o'lchov: 0.7:1, codeburn #read-edit-ratio).
- Destructive/physical (o'chirish, to'lov, chop etish, uskuna) — rejani ko'rsat, ruxsat ol, backup ol.
- Kichik, qaytariladigan qadamlar. Katta diff yo'q.
- O'zbek UTF-8 belgilarni buzma (o‘/g‘/sh/ch).

## Arxitektura chegaralari (buzilmaydi)
- `packs/*.yaml` — mijoz konfigi. Core kod pack mazmuniga bog'lanmaydi.
- Kalitlar faqat env/vault'da. Kodda secret yo'q.
- Runner'da miya yo'q — barcha LLM chaqiruvlar API orqali.
- Yangi endpoint eski route'ni o'zgartirmaydi (OCP).

## Ish tartibi
- TDD: avval failing test, keyin minimal kod. Test'siz prod kod yo'q.
- Har pack o'zgarishi: 10 ta test yashil bo'lmasa commit yo'q.
- Har javob oxirida: o'zgargan fayllar, test natijasi, keyingi qadam.

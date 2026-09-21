# PRD-04: o‘zbekcha ovoz, streaming va iqtisod

## Arxitektura qarori

TTS va STT bir providerda bo‘lishi shart emas. `SpeechProvider` abstraction capability asosida tanlanadi: Uzbek, sheva, streaming-input, partial-transcript, diarization, timestamp, telephony codec, commercial rights va privacy. Eng arzon variant faqat kerakli quality gate’dan o‘tgandan keyin tanlanadi.

Boshlang‘ich tavsiya: o‘zbekcha TTS uchun Aisha asosiy sinov nomzodi. STT uchun Aisha va Google Cloud Uzbek qo‘llovchi region/model konfiguratsiyasi bir xil lokal audio korpusda solishtiriladi. Google Dynamic Batch arzon yozuv transkripsiyasi nomzodi; live operator uchun emas.

## Tariflarni to‘g‘ri talqin qilish

Foydalanuvchi yuborgan Starter/Creator/Pro/Startup/Scale narxlari oylik deb ko‘rsatilgan. Ochiq VoiceLab sahifasida yillik to‘lov ekvivalentlari ko‘rindi: $50/$200/$900/$2,890/$8,990 yiliga. Bu avtomatik ravishda foydalanuvchi oylik narxlari noto‘g‘ri degani emas. Billing periodlar farqli. Pro uchun $90 va 250,000 UZS o‘rtasidagi nomuvofiqlik tasdiqlanishi kerak.

VoiceLab abonement krediti Aisha backend API balansiga bir xil tatbiq etilishi ochiq hujjatda tasdiqlanmadi. Aisha API pricing sahifasi STT 425 UZS/minut, TTS 1 UZS/belgi va monthly fee yo‘qligini ko‘rsatadi. Narxlar shartnoma va hisobdan tekshiriladi, code’da doimiy haqiqat sifatida hardcode qilinmaydi.

Barcha xizmatlar bitta kredit balansini ishlatadi. Paketdagi maksimal STT, TTS va voice-agent soatlarini qo‘shib bo‘lmaydi. TTS belgi narxini output minutiga aniq o‘tkazish mumkin emas: matn uzunligi va gapirish tezligi ta’sir qiladi.

| Nomzod | Ochiq narx | Izoh |
|---|---|---|
| Aisha API STT | 425 UZS/min | Uzbek va realtime API hujjatlangan, live sinov qilinmagan |
| Aisha API TTS | 1 UZS/belgi | Uzbek Gulnoza, API abonementdan alohida aniqlashtiriladi |
| Google V2 standard STT | $0.016/min boshlang‘ich tier | Streaming uchun region/model/til support tekshiriladi |
| Google V2 dynamic batch | $0.003/min | Navbatli asinxron yozuv, live qo‘ng‘iroqqa mos emas |
| Deepgram Whisper batch | $0.0048/min ochiq narx | Uzbek hosted Whisper, Nova/Flux Uzbek streaming deb talqin qilinmaydi |

Narxlar storage, egress, LLM, telephony, diarization qo‘shimchalari va retry xarajatini qoplamasligi mumkin. Google multi-channel audio billingini ham inobatga olish kerak. Self-hosted Whisper bepul xizmat emas, compute va operatsion xarajatga ega.

## Streaming nimani anglatadi

Call operatorga live audio ingest, VAD/end-of-turn, partial STT, agent loop, TTS playback, barge-in, cancellation va human handoff kerak. Meetingga live subtitr uchun STT stream zarur; keyin transkript/xulosa yetarli bo‘lsa batch arzonroq bo‘lishi mumkin. Meeting uchun odatda TTS shart emas.

Aisha STT WebSocket WebM/Opus konteyner yoki 16 kHz mono s16le PCM qabul qiladi. TTS WebSocket metadata va complete WAV frame bilan hujjatlangan. Bu incremental token-to-audio yoki millisekundlik telephony latency kafolati emas. 8 kHz μ-law/A-law telefon audiosi gateway’da codec conversion talab qilishi mumkin. Public concurrency/SLA, SIP/handoff va rate limit shartlari aniqlashtirilmagan.

| ID | Talab | Qabul mezoni |
|---|---|---|
| VO-01 | Provider ajratilishi | STT va TTS alohida konfiguratsiya; capabilities mos kelmasa fail-closed |
| VO-02 | Uzbek quality corpus | Kamida 100 rozilikli yozuv, turli sheva, ruscha aralash, shovqin, ism/raqam |
| VO-03 | STT sifat | WER/CER va muhim maydon accuracy alohida; sheva bo‘yicha natijalar |
| VO-04 | Live ishlash | p50/p95 first partial, end-of-turn, first audio va overall response o‘lchanadi |
| VO-05 | Ovoz uzilishi | Barge-in playbackni to‘xtatadi, stale turn javobi yuborilmaydi |
| VO-06 | To‘lov | Atomic budget reservation, per-provider usage event, invoice reconciliation |
| VO-07 | Maxfiylik | Consent, retention, delete, DPA, region va subprocessor shartlari |
| VO-08 | Recovery | Disconnect/resume, duplicate chunk, timeout, provider fallback va handoff |

Dastlabki quality maqsadi WER ≤15%, muhim ism/raqamlar ≥98% to‘g‘ri; live javob p95 <2.5 soniya nomzod mezon. Bu kelishiladigan acceptance targetlar, o‘lchangan natija yoki vendor kafolati emas. Sifatni tekshirmasdan eng arzon provider avtomatik productionga tanlanmaydi.

## Ushbu paketda nima implement qilingan

`platform_runtime/speech.py`: Aisha Uzbek REST TTS multipart request, `audio_path` validation; STT multipart audio va `transcript` parse. Fixed HTTPS origin, X-Api-Key header, timeout, bounded response, redirect va automatic retry yo‘q. 1 MB STT limit mahsulotning lokal ehtiyot cheklovi, Aisha v1 maksimumi deb ko‘rsatilmaydi. TTS task tool faqat text/mood oladi; speed faqat pastki adapter metodida mavjud.

`voice.tts` pullik tashqi write sifatida approvalga tushadi. REST response’dagi audio path avtomatik yuklab olinmaydi; media download auth kontrakti aniqlashtirilmagan. STT adapteri public upload endpointga ulanmagan. Streaming, call operator, meeting bot, budget meter va audio retention implement qilinmagan. Barcha speech testlar mock transport, haqiqiy provider audiosi ishlab chiqarilmagan.

## Rasmiy manbalar

- https://aisha.group/en/pricing
- https://voicelab.uz/pricing
- https://aisha.group/en/api-documentation/text-to-speech
- https://aisha.group/en/api-documentation/speech-to-text
- https://voicelab.uz/privacy
- https://voicelab.uz/terms
- https://cloud.google.com/speech-to-text/pricing
- https://docs.cloud.google.com/speech-to-text/docs/speech-to-text-supported-languages
- https://docs.cloud.google.com/speech-to-text/docs/models/chirp-2
- https://deepgram.com/pricing
- https://developers.deepgram.com/docs/deepgram-whisper-cloud

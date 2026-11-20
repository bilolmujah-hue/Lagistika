# Logistika yordamchi bot — tijoriy versiya

## Ishga tushirish
1. `.env.example` → `.env` (yoki Railway Variables). `ALLOWED_USERS` = sizning ID (bosh admin).
2. `SESSION_KEY` yarating (`.env.example` ichida buyruq bor) — keyin hech qachon o'zgartirmang.
3. Railway'da **Volume** ulang va `DB_PATH=/data/logistika.db` qiling (aks holda har deployda baza o'chadi).
4. Eski `logistika.db` ni o'sha joyga qo'ying — birinchi ishga tushishda avtomatik yangilanadi (furalar,
   hujjatlar, eski userbot va uning guruhlari saqlanadi).
5. `pip install -r requirements.txt` → `python bot.py`
6. Botga `/start` bosing, raqam yuboring, «Menejer» ni tanlang → `🛠 Admin panel` tugmasi chiqadi.

## Birinchi sozlash (Admin panel)
1. **⚙️ Avto to'lov** → karta raqami, karta egasi, narx, muddat, sinov kunlari.
2. **⚙️ Avto to'lov → To'lov akkauntini ulash** — @CardXabarBot ulangan Telegram akkaunt.
   Shu akkauntdan @CardXabarBot ga kartani ulang. «🧪 CardXabar ni tekshirish» bilan sinab ko'ring.
3. **🤖 Userbotlar → ➕ Akkaunt qo'shish** — yuk qidirish uchun 10 tagacha akkaunt. Har biriga yuk guruhlarini tanlang.
   Alohida (shaxsiy bo'lmagan) raqamlar ishlating.

## Yuk qidirish (yangi)
- Foydalanuvchi «🔎 Yuk qidirish» ni bosib **«Toshkent Andijon»** deb yozsa - qidiruv darhol boshlanadi (savol berilmaydi).
  «... fura kerak / mashina kerak» desa - bo'sh mashina qidiriladi. Oddiy so'rovlar AI siz tushuniladi.
- Admin qo'shgan **barcha userbotlar bir vaqtda** ishlaydi: har biri a'zo bo'lgan barcha guruhlardan Telegram global
  qidiruvi bilan (1 ta so'rov = 30-50 guruh) oxirgi **24 soatlik** e'lonlarni oladi. Kam chiqsa - guruhma-guruh zaxira qidiruv.
- Odatda 1-3 daqiqada (eng ko'pi `SEARCH_TIMEOUT`=150 s) **5 ta** eng mos e'lon. Qidiruv paytida jonli holat ko'rinadi.
- Natija ostida: «🤖 Bot gaplashsin», «🔄 Yana 5 ta qidirish» (oldin ko'rsatilmagan BOSHQA odamlar), «🙋 O'zim yozaman».
- «Bot gaplashsin» - mijozlarga **foydalanuvchining o'z Telegram akkauntidan** yoziladi. Akkaunt faqat bir marta ulanadi
  (telefon → kod → 2FA) va keyin qidiruvda ham, «📢 Avto xabar» da ham shu akkaunt ishlatiladi.
- Aniq e'lonlar AI siz qabul qilinadi, faqat shubhalilari arzon AI ga ketadi (token tejaladi, natijalar keshlanadi).

## VIP tarif
Admin «⭐ VIP berish» qilgan foydalanuvchida barcha bot limitlari yo'q: kunlik yozish, bir qidiruvdagi yozish,
avto xabarning guruh va kunlik limitlari. (Telegram'ning o'z spam cheklovlari baribir hisobga olinadi - akkaunt ban bo'lmasligi uchun.)

## AI kalitlar (🛠 Admin panel → 🧠 AI kalitlar)
Yuk qidiruvidagi ommaviy ishlar arzon AI larda bajariladi: Groq, OpenRouter (bepul «:free» modellar), Gemini,
DeepSeek, OpenAI yoki istalgan OpenAI-mos API. «➕ AI qo'shish» → xizmat → kalit → model; bot darhol sinab ko'radi.
AI lar **navbatma-navbat** ishlatiladi (🔄 Navbat tugmasi) - yuk bitta AI ga tushmaydi. Kalit limiti tugasa, ishlamasa
yoki ketma-ket 3 marta xato bersa - bot keyingisiga o'tadi va adminga xabar yuboradi. Hammasi ishlamasa - Claude.

## Userbotlar barqarorligi
Uzilib qolgan akkauntlar har 90 soniyada avtomatik qayta ulanadi; ulanmasa adminga xabar keladi.
Admin panel → 🤖 Userbotlar → «🔄 Hammasini qayta ulash». FloodWait olgan akkaunt qidiruvdan vaqtincha chetlanadi.

## To'lov qanday aniqlanadi
Har bir to'lovchiga unikal summa beriladi (masalan **100 347** so'm). @CardXabarBot xabarida «➕ 100 347.00 UZS»
kelishi bilan obuna avtomatik ochiladi. Chek AI orqali o'qiladi, lekin obuna faqat kartaga **haqiqiy tushum**
topilganda ochiladi (soxta chek o'tmaydi). Tushum topilmasa, chek adminga «Tasdiqlash / Rad etish» bilan boradi.
Bitta tushum faqat bitta to'lovga bog'lanadi.

## Fayllar
| Fayl | Vazifa |
|---|---|
| bot.py | asosiy: ro'yxatdan o'tish, menyular, rollar, tugmalar |
| admin.py | admin panel |
| payments.py | to'lov: unikal summa, taymer, CardXabar, chek |
| userbot.py, login.py, pickers.py | Telegram akkauntlar (pool / to'lov / foydalanuvchi) |
| cargo.py | yuk/fura qidirish va egalari bilan AI suhbat |
| autopost.py | guruhlarga avto xabar |
| gps.py | Wialon (har foydalanuvchiga o'z tokeni) |
| ai.py | Claude: hujjat/chek o'qish, rolga qarab tool'lar |
| reminders.py | hujjat, navbat va obuna eslatmalari (menejer + haydovchi) |
| database.py | SQLite + avtomatik migratsiya |

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

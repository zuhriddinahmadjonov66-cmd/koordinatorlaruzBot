# KoordinatorlarUZ Bot

Telegram bot polling rejimida ishlaydi; aiohttp HTTP serveri Render va UptimeRobot uchun `/` va `/health` manzillarini ochadi.

## Render sozlamalari

1. GitHub repositoryga shu papkadagi fayllarni joylang. `.env` va `bot_database.db` fayllarini yuklamang.
2. Render’da **Web Service** tanlang va repositoryni ulang.
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `python -u bot.py`
5. Environment Variables: `BOT_TOKEN`, `OWNER_ID`, `PROMO_CODE`, `ADMIN_CONTACT`; `PORT` uchun Render bergan qiymatni ishlating yoki `10000` kiriting.
6. Health Check Path: `/health`.

Render’da bitta faol service shu bot tokeni bilan ishlasin. Ikkinchi service yoki kompyuterdagi lokal bot ham polling qilsa Telegram `409 Conflict` qaytaradi.

## Eslatma

SQLite baza lokal faylda saqlanadi. Render restart yoki qayta deploy bo‘lganda ma’lumot yo‘qolmasligi uchun pullik Web Service’ga Persistent Disk ulang va `DATABASE_PATH=/var/data/bot_database.db` environment variable kiriting.

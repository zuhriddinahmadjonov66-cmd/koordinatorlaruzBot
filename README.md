# Koordinatorlar Platformasi Telegram Boti (@KoordinatorlarUzBot)

Ushbu bot koordinatorlar ishini samarali tashkil etish, topshiriqlar berish hamda hisobotlarni baholash uchun mo'ljallangan.

## GitHub va Render.com da ishga tushirish

### 1. GitHub repository yaratish va fayllarni yuklash
Quyidagi fayllarni yangi repository'ga yuklang:
- `bot.py`
- `config.json`
- `questions.json`
- `requirements.txt`
- `.gitignore`
- `README.md`

### 2. Render.com da Web Service yaratish
1. [Render.com](https://render.com) saytiga kiring va **New + -> Web Service** tanlang.
2. GitHub repository'ingizni ulang.
3. Quyidagi sozlamalarni kiriting:
   - **Name**: `koordinatorlar-bot`
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python -u bot.py`
   - **Health Check Path**: `/health`

4. **Environment Variables** bo'limida quyidagi o'zgaruvchilarni qo'shing:
   - `BOT_TOKEN`: Telegram Bot Tokeningiz
   - `OWNER_ID`: Bosh admin Telegram ID (masalan: `123456789`)
   - `PROMO_CODE`: Maxfiy promo-kod (masalan: `PROMO2026`)
   - `ADMIN_CONTACT`: Admin bilan bog'lanish havolasi (masalan: `@admin`)
   - `PORT`: `8080`

5. **Deploy Service** tugmasini bosing.

## Yaratuvchi
**Zuhriddin Ahmadjonov**

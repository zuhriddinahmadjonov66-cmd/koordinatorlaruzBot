import os
import json
import logging
import asyncio
import sqlite3
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from aiohttp import web
from pathlib import Path

# Logging sozlamalari (Maxfiy ma'lumotlar logga chiqmaydi)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Konfiguratsiya va Envlarni yuklash
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
PROMO_CODE = os.getenv("PROMO_CODE", "").strip()
ADMIN_CONTACT = os.getenv("ADMIN_CONTACT", "@admin").strip()
PORT = int(os.getenv("PORT", "8080"))

ROOT = Path(__file__).resolve().parent

# JSON fayllarni o'qish
def load_json(filepath: str) -> dict:
    path = ROOT / filepath
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}

config_data = load_json("config.json")
questions_data = load_json("questions.json").get("questions", [])

BOT_NAME = config_data.get("name", "Koordinatorlar platformasi")
DIRECTIONS = config_data.get("directions", [])
REGIONS = ["Andijon", "Buxoro", "Farg‘ona", "Jizzax", "Namangan", "Navoiy", "Qashqadaryo", "Qoraqalpog‘iston Respublikasi", "Samarqand", "Sirdaryo", "Surxondaryo", "Toshkent viloyati", "Toshkent shahri", "Xorazm"]
if len(DIRECTIONS) != 8 or len(set(DIRECTIONS)) != 8:
    raise RuntimeError("config.json ichida takrorlanmaydigan 8 ta yo‘nalish bo‘lishi kerak.")

# Database boshqaruvi
DB_PATH = os.getenv("DATABASE_PATH", str(ROOT / "bot_database.db"))

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Foydalanuvchilar jadvali
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        full_name TEXT,
        region TEXT,
        district TEXT,
        school TEXT,
        direction TEXT,
        role TEXT DEFAULT 'koordinator',
        is_approved INTEGER DEFAULT 0,
        is_pro INTEGER DEFAULT 0,
        pro_expiry TEXT,
        referrer_id INTEGER,
        referral_count INTEGER DEFAULT 0,
        promo_used INTEGER DEFAULT 0,
        registered_at TEXT
    )
    """)
    
    # Topshiriqlar jadvali
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        description TEXT,
        direction TEXT,
        region TEXT,
        deadline TEXT,
        created_by INTEGER,
        created_at TEXT
    )
    """)
    
    # Hisobotlar jadvali
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER,
        user_id INTEGER,
        content_type TEXT,
        content TEXT,
        status TEXT DEFAULT 'Baholash kutilmoqda',
        score INTEGER DEFAULT 0,
        feedback TEXT,
        submitted_at TEXT
    )
    """)
    
    # DLS Turnir jadvali
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dls_registrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        full_name TEXT,
        dls_nickname TEXT,
        phone TEXT,
        registered_at TEXT
    )
    """)
    # Oldingi baza bilan ham mos ishlashi uchun yangi ustunni xavfsiz qo‘shadi.
    dls_columns = {row[1] for row in cursor.execute("PRAGMA table_info(dls_registrations)")}
    if "telegram_username" not in dls_columns:
        cursor.execute("ALTER TABLE dls_registrations ADD COLUMN telegram_username TEXT DEFAULT ''")
    
    # Test Natijalari jadvali
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS quiz_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        score INTEGER,
        total INTEGER,
        percentage REAL,
        completed_at TEXT
    )
    """)

    # Bosh administrator botga birinchi marta yozganda tasdiqlashni kutib qolmaydi.
    if OWNER_ID > 0:
        cursor.execute("""
        INSERT OR IGNORE INTO users
            (user_id, full_name, role, is_approved, registered_at)
        VALUES (?, 'Bosh administrator', 'bosh_admin', 1, ?)
        """, (OWNER_ID, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    
    conn.commit()
    conn.close()

init_db()

def user_has_pro(user) -> bool:
    if not user or not user[8] or not user[9]:
        return False
    try:
        return datetime.strptime(user[9], "%Y-%m-%d").date() >= datetime.now().date()
    except ValueError:
        return False

# FSM Steytlar
class RegistrationStates(StatesGroup):
    full_name = State()
    region = State()
    district = State()
    school = State()
    direction = State()

class TaskCreationStates(StatesGroup):
    direction = State()
    region = State()
    title = State()
    description = State()
    deadline = State()

class ReportStates(StatesGroup):
    task_id = State()
    content = State()

class DLSStates(StatesGroup):
    full_name = State()
    dls_nickname = State()
    phone = State()

class SupportStates(StatesGroup):
    message = State()

class PromoStates(StatesGroup):
    code = State()

class ReviewReportStates(StatesGroup):
    report_id = State()
    action = State()
    score = State()
    feedback = State()

class QuizStates(StatesGroup):
    current_index = State()
    correct_answers = State()

# Klaviatura generatorlari
def main_menu_keyboard(user_role: str, is_approved: bool):
    buttons = []
    if is_approved:
        buttons.extend([
            [KeyboardButton(text="🧭 Koordinator paneli"), KeyboardButton(text="📋 Topshiriqlar")],
            [KeyboardButton(text="📤 Hisobot yuborish"), KeyboardButton(text="📖 Koordinator nima?")],
            [KeyboardButton(text="⭐ PRO obuna"), KeyboardButton(text="🎟 Promo kod")],
            [KeyboardButton(text="🏆 Turnir va o‘yinlar"), KeyboardButton(text="🆘 Adminga murojaat")],
            [KeyboardButton(text="ℹ️ Bot haqida"), KeyboardButton(text="👤 Profilim")]
        ])
    else:
        buttons.append([KeyboardButton(text="👤 Profilim"), KeyboardButton(text="ℹ️ Bot haqida")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# Bot va Dispatcher yaratish
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=BOT_TOKEN)

# Database yordamchi funksiyalari
def get_user(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res

def get_task(task_id: Optional[int]):
    if not task_id:
        return None
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    res = cursor.fetchone()
    conn.close()
    return res

def register_user(user_id, full_name, region, district, school, direction, referrer_id=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    role = 'bosh_admin' if user_id == OWNER_ID else 'koordinator'
    is_approved = 1 if user_id == OWNER_ID else 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("""
    INSERT INTO users (user_id, full_name, region, district, school, direction, role, is_approved, referrer_id, registered_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET
        full_name=excluded.full_name, region=excluded.region,
        district=excluded.district, school=excluded.school,
        direction=excluded.direction
    """, (user_id, full_name, region, district, school, direction, role, is_approved, referrer_id, now))
    
    conn.commit()
    conn.close()

# Bot Handlerlari
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user = get_user(message.from_user.id)
    args = message.text.split()
    referrer_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    if referrer_id == message.from_user.id:
        referrer_id = None

    if not user:
        await state.update_data(referrer_id=referrer_id)
        await message.answer(f"Xush kelibsiz! **{BOT_NAME}** botiga ro‘yxatdan o‘tish uchun ism va familiyangizni kiriting:")
        await state.set_state(RegistrationStates.full_name)
    else:
        is_approved = bool(user[7])
        role = user[6]
        text = "Siz ro‘yxatdan o‘tgansiz."
        if not is_approved:
            text += "\n⚠️ Akkauntingiz admin tasdiqlashini kutmoqda."
        await message.answer(text, reply_markup=main_menu_keyboard(role, is_approved))

@dp.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(f"Sizning Telegram IDingiz: `{message.from_user.id}`")

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer("Yordam:\n/start — ro‘yxatdan o‘tish\n/id — Telegram ID\n/rol ID ROL — mas’ul tayinlash (faqat bosh admin)\n\nBotdan foydalanish uchun administrator tasdig‘ini kuting.")

@dp.message(Command("rol"))
async def set_role(message: Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("Bu amal faqat bosh administrator uchun.")
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in ("koordinator", "viloyat_masuli", "tuman_masuli"):
        await message.answer("Ishlatish: /rol TELEGRAM_ID koordinator|viloyat_masuli|tuman_masuli")
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET role=?, is_approved=1 WHERE user_id=?", (parts[2], int(parts[1])))
    changed = cursor.rowcount
    conn.commit()
    conn.close()
    await message.answer("Rol yangilandi." if changed else "Foydalanuvchi topilmadi. U avval botda /start bosishi kerak.")

# Ro'yxatdan o'tish FSM
@dp.message(RegistrationStates.full_name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(full_name=message.text)
    await message.answer("Viloyatingizni kiriting:")
    await state.set_state(RegistrationStates.region)

@dp.message(RegistrationStates.region)
async def process_region(message: Message, state: FSMContext):
    await state.update_data(region=message.text)
    await message.answer("Tumaningizni kiriting:")
    await state.set_state(RegistrationStates.district)

@dp.message(RegistrationStates.district)
async def process_district(message: Message, state: FSMContext):
    await state.update_data(district=message.text)
    await message.answer("Maktabingizni kiriting:")
    await state.set_state(RegistrationStates.school)

@dp.message(RegistrationStates.school)
async def process_school(message: Message, state: FSMContext):
    await state.update_data(school=message.text)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=d)] for d in DIRECTIONS],
        resize_keyboard=True, one_time_keyboard=True
    )
    await message.answer("Quyidagi yo‘nalishlardan birini tanlang:", reply_markup=kb)
    await state.set_state(RegistrationStates.direction)

@dp.message(RegistrationStates.direction)
async def process_direction(message: Message, state: FSMContext):
    if message.text not in DIRECTIONS:
        await message.answer("Iltimos, tugmalardan birini tanlang.")
        return
    data = await state.get_data()
    register_user(
        message.from_user.id,
        data['full_name'],
        data['region'],
        data['district'],
        data['school'],
        message.text,
        data.get('referrer_id')
    )
    await state.clear()
    await message.answer(
        "Ro‘yxatdan o‘tish muvaffaqiyatli yakunlandi! ⏳ Admin tasdiqlashini kuting.",
        reply_markup=main_menu_keyboard('koordinator', False)
    )
    if OWNER_ID:
        try:
            approve_kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"approve_{message.from_user.id}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_{message.from_user.id}")
            ]])
            await bot.send_message(
                OWNER_ID,
                f"🆕 Yangi foydalanuvchi:\nIsm: {data['full_name']}\nHudud: {data['region']}, {data['district']}\nMaktab: {data['school']}\nYo‘nalish: {message.text}",
                reply_markup=approve_kb
            )
        except Exception as e:
            logger.error(f"Admin warning sent failed: {e}")

@dp.callback_query(F.data.startswith("approve_"))
async def approve_user(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Bu amal faqat bosh administrator uchun.", show_alert=True)
        return
    user_id = int(call.data.split("_")[1])
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT is_approved, referrer_id FROM users WHERE user_id = ?", (user_id,))
    existing = cursor.fetchone()
    if not existing:
        conn.close()
        await call.answer("Foydalanuvchi topilmadi.", show_alert=True)
        return
    if existing[0]:
        conn.close()
        await call.answer("Bu foydalanuvchi allaqachon tasdiqlangan.", show_alert=True)
        return
    cursor.execute("UPDATE users SET is_approved = 1 WHERE user_id = ?", (user_id,))
    if existing[1] and existing[1] != user_id:
        referrer_id = existing[1]
        cursor.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?", (referrer_id,))
        cursor.execute("SELECT referral_count FROM users WHERE user_id = ?", (referrer_id,))
        ref_row = cursor.fetchone()
        c = ref_row[0] if ref_row else 0
        if c == 5:
            # 1 oy PRO berish
            expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
            cursor.execute("UPDATE users SET is_pro = 1, pro_expiry = ? WHERE user_id = ?", (expiry, referrer_id))
            try:
                await bot.send_message(referrer_id, "🎉 Siz 5 ta do‘stingizni taklif qildingiz va 1 oylik PRO a'zolikka ega bo‘ldingiz!")
            except Exception:
                pass
    conn.commit()
    conn.close()
    await call.message.edit_text("Foydalanuvchi tasdiqlandi!")
    await call.answer("Tasdiqlandi")
    try:
        await bot.send_message(user_id, "✅ Sizning akkauntingiz admin tomonidan tasdiqlandi!")
    except Exception:
        pass

@dp.callback_query(F.data.startswith("reject_"))
async def reject_user(call: CallbackQuery):
    if call.from_user.id != OWNER_ID:
        await call.answer("Bu amal faqat bosh administrator uchun.", show_alert=True)
        return
    user_id = int(call.data.split("_")[1])
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM users WHERE user_id = ? AND user_id != ?", (user_id, OWNER_ID))
    conn.commit()
    conn.close()
    await call.message.edit_text("Ariza rad etildi.")
    await call.answer("Rad etildi")
    try:
        await bot.send_message(user_id, "Arizangiz hozircha tasdiqlanmadi. Ma’lumotlarni tekshirib, /start orqali qayta yuboring.")
    except Exception:
        pass

# Menyu Tugmalari Handlerlari
@dp.message(F.text == "👤 Profilim")
async def profile_handler(message: Message):
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Siz ro‘yxatdan o‘tmagansiz. /start bosing.")
        return
    pro_status = "Aktiv ⭐" if user_has_pro(user) else "Noaktiv"
    expiry = user[9] if user[9] else "-"
    text = (
        f"👤 **Profil ma’lumotlari:**\n\n"
        f"Ism: {user[1]}\n"
        f"Hudud: {user[2]}, {user[3]}\n"
        f"Maktab: {user[4]}\n"
        f"Yo‘nalish: {user[5]}\n"
        f"Rol: {user[6]}\n"
        f"PRO status: {pro_status} (Tugash sanasi: {expiry})\n"
        f"Taklif qilgan do'stlar: {user[11]} ta\n"
        f"Taklif havolangiz: `https://t.me/KoordinatorlarUzBot?start={message.from_user.id}`"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "ℹ️ Bot haqida")
async def about_handler(message: Message):
    text = (
        f"🤖 **{BOT_NAME}**\n\n"
        f"Ushbu bot koordinatorlar va mas'ullar o'rtasidagi topshiriqlar hamda hisobotlar jarayonini avtomatlashtirish uchun mo'ljallangan.\n\n"
        f"👨‍💻 Yaratuvchi: **Zuhriddin Ahmadjonov**\n"
        f"Bog‘lanish: {ADMIN_CONTACT}"
    )
    await message.answer(text)

@dp.message(F.text == "📖 Koordinator nima?")
async def coordinator_info(message: Message):
    text = (
        "📖 **Koordinator nima?**\n\n"
        "Koordinator — belgilangan yo‘nalish va hududda tadbirlar, loyihalar va topshiriqlarni samarali tashkil etuvchi, "
        "jamoani boshqaruvchi hamda natijalar bo‘yicha hisobot beruvchi mas’ul shaxsdir.\n\n"
        "_Qo'shimcha ma'lumotlar administrator tomonidan yangilanib boriladi._"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "⭐ PRO obuna")
async def pro_info(message: Message):
    user = get_user(message.from_user.id)
    if user_has_pro(user):
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📸 Media qo‘llanma", callback_data="pro_media")],
            [InlineKeyboardButton(text="📘 Koordinator darsligi", callback_data="pro_lessons")]
        ])
        await message.answer("⭐ Siz PRO a'zosiz! Kerakli bo‘limni tanlang:", reply_markup=kb)
    else:
        text = (
            "⭐ **PRO obuna**\n\n"
            "Narxi: **10 000 so‘m / oy**\n\n"
            "PRO Imkoniyatlari:\n"
            "- 📸 Media qo‘llanma (rasm va hisobot tayyorlash)\n"
            "- 📘 Eksklyuziv koordinator darsliklari\n\n"
            "Obunani faollashtirish uchun administrator bilan bog‘laning:\n"
            f"📞 Admin: {ADMIN_CONTACT}"
        )
        await message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data == "pro_media")
async def pro_media_cb(call: CallbackQuery):
    user = get_user(call.from_user.id)
    if not user_has_pro(user):
        await call.answer("Bu bo‘lim faqat faol PRO obunachilar uchun.", show_alert=True)
        return
    text = "📸 **Media qo‘llanma:**\n1. Rasm sifatli va yorug‘ joyda olingan bo‘lishi kerak.\n2. Hisobot ob’ekti aniq aks etishi lozim.\n3. Hujjatlar ko‘rinarli skanerlangan bo‘lishi shart."
    await call.message.answer(text, parse_mode="Markdown")
    await call.answer()

@dp.callback_query(F.data == "pro_lessons")
async def pro_lessons_cb(call: CallbackQuery):
    user = get_user(call.from_user.id)
    if not user_has_pro(user):
        await call.answer("Bu bo‘lim faqat faol PRO obunachilar uchun.", show_alert=True)
        return
    text = "📘 **Koordinator darsligi:**\n1. Reja tuzish va vaqtni boshqarish.\n2. Jamoani shakllantirish va vazifalar taqsimoti.\n3. Natijador hisobot tayyorlash va tahlil."
    await call.message.answer(text, parse_mode="Markdown")
    await call.answer()

@dp.message(F.text == "🎟 Promo kod")
async def promo_start(message: Message, state: FSMContext):
    await message.answer("Iltimos, promo kodni kiriting:")
    await state.set_state(PromoStates.code)

@dp.message(PromoStates.code)
async def promo_process(message: Message, state: FSMContext):
    user = get_user(message.from_user.id)
    if not user:
        await state.clear()
        await message.answer("Avval /start orqali ro‘yxatdan o‘ting.")
        return
    if not PROMO_CODE:
        await state.clear()
        await message.answer("Promo kod hozircha sozlanmagan. Administratorga murojaat qiling.")
        return
    if user[12] == 1:
        await message.answer("Siz promo koddan foydalanib bo‘lgansiz!")
        await state.clear()
        return

    code_input = message.text.strip()
    if code_input == PROMO_CODE:
        expiry = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_pro = 1, pro_expiry = ?, promo_used = 1 WHERE user_id = ?", (expiry, message.from_user.id))
        conn.commit()
        conn.close()
        await message.answer("🎉 Promo kod to‘g‘ri! Sizga 1 oylik PRO obuna berildi.")
    else:
        await message.answer("❌ Noto‘g‘ri promo kod!")
    await state.clear()

@dp.message(F.text == "🆘 Adminga murojaat")
async def support_start(message: Message, state: FSMContext):
    await message.answer("Murojaatingiz yoki xatolik haqidagi xabaringizni yozib qoldiring:")
    await state.set_state(SupportStates.message)

@dp.message(SupportStates.message)
async def support_process(message: Message, state: FSMContext):
    msg = message.text
    await state.clear()
    await message.answer("Murojaatingiz adminga yuborildi. Rahmat!")
    if OWNER_ID:
        try:
            await bot.send_message(OWNER_ID, f"🆘 **Yangi murojaat** ({message.from_user.id}):\n\n{msg}")
        except Exception:
            pass

# Turnirlar va O'yinlar
@dp.message(F.text == "🏆 Turnir va o‘yinlar")
async def games_menu(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 DLS Turniriga yozilish", callback_data="dls_reg")],
        [InlineKeyboardButton(text="🧠 Dunyoviy bilim testi", callback_data="quiz_start")]
    ])
    await message.answer("Kerakli bo‘limni tanlang:", reply_markup=kb)

@dp.callback_query(F.data == "dls_reg")
async def dls_start(call: CallbackQuery, state: FSMContext):
    await call.message.answer("DLS Turniri uchun ism-familiyangizni kiriting:")
    await state.set_state(DLSStates.full_name)

@dp.message(DLSStates.full_name)
async def dls_name(message: Message, state: FSMContext):
    await state.update_data(full_name=message.text)
    await message.answer("DLS akkaunt nomingizni kiriting:")
    await state.set_state(DLSStates.dls_nickname)

@dp.message(DLSStates.dls_nickname)
async def dls_nick(message: Message, state: FSMContext):
    await state.update_data(dls_nickname=message.text)
    await message.answer("Telefon raqamingizni kiriting:")
    await state.set_state(DLSStates.phone)

@dp.message(DLSStates.phone)
async def dls_phone(message: Message, state: FSMContext):
    data = await state.get_data()
    phone = message.text
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    username = f"@{message.from_user.username}" if message.from_user.username else "Telegram username yo‘q"
    cursor.execute("""
    INSERT INTO dls_registrations (user_id, full_name, dls_nickname, phone, registered_at, telegram_username)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (message.from_user.id, data['full_name'], data['dls_nickname'], phone,
          datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("✅ DLS Turniriga muvaffaqiyatli yozildingiz!\n🗓 Turnir sanasi: **Tez kunda**", parse_mode="Markdown")
    if OWNER_ID:
        try:
            await bot.send_message(OWNER_ID, f"🎮 DLS turniriga yozilish\nIsm: {data['full_name']}\nDLS nomi: {data['dls_nickname']}\nTelegram: {username}\nTelefon: {phone}")
        except Exception:
            logger.exception("DLS arizasini adminga yuborib bo‘lmadi")

# Quiz Handlerlari
@dp.callback_query(F.data == "quiz_start")
async def quiz_start(call: CallbackQuery, state: FSMContext):
    if not questions_data:
        await call.message.answer("Savollar bazasi bo‘sh.")
        return
    await state.update_data(current_index=0, correct_answers=0)
    await send_quiz_question(call.message, 0, state)
    await call.answer()

async def send_quiz_question(message: Message, index: int, state: FSMContext):
    if index >= len(questions_data) or index >= 15:
        data = await state.get_data()
        correct = data.get('correct_answers', 0)
        total = min(len(questions_data), 15)
        percentage = (correct / total) * 100
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO quiz_results (user_id, score, total, percentage, completed_at) VALUES (?, ?, ?, ?, ?)",
                       (message.chat.id, correct, total, percentage, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        
        await message.answer(f"🎉 Test yakunlandi!\n\nTo‘g‘ri javoblar: {correct}/{total}\nNatija: {percentage:.1f}%")
        cursor = sqlite3.connect(DB_PATH).cursor()
        cursor.execute("SELECT user_id, MAX(percentage), MAX(completed_at) FROM quiz_results GROUP BY user_id ORDER BY MAX(percentage) DESC, MAX(completed_at) ASC LIMIT 10")
        leaders = cursor.fetchall()
        cursor.connection.close()
        ranking = "🏅 Test reytingi (TOP 10):\n" + "\n".join(
            f"{i}. {get_user(row[0])[1] if get_user(row[0]) else row[0]} — {row[1]:.0f}%" for i, row in enumerate(leaders, 1)
        )
        await message.answer(ranking)
        await state.clear()
        return

    q = questions_data[index]
    buttons = []
    for idx, opt in enumerate(q['options']):
        buttons.append([InlineKeyboardButton(text=opt, callback_data=f"quiz_ans_{idx}")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(f"❓ **{index+1}-savol:** {q['question']}", reply_markup=kb, parse_mode="Markdown")
    await state.set_state(QuizStates.current_index)

@dp.callback_query(F.data.startswith("quiz_ans_"))
async def quiz_answer(call: CallbackQuery, state: FSMContext):
    if not await state.get_state():
        await call.answer("Avval testni boshlang.", show_alert=True)
        return
    ans_idx = int(call.data.split("_")[2])
    data = await state.get_data()
    index = data.get('current_index', 0)
    correct_cnt = data.get('correct_answers', 0)

    if index < len(questions_data):
        if ans_idx == questions_data[index]['correct']:
            correct_cnt += 1
        await state.update_data(current_index=index + 1, correct_answers=correct_cnt)
        await call.message.delete()
        await send_quiz_question(call.message, index + 1, state)
    await call.answer()

# Topshiriqlar va Hisobotlar
@dp.message(F.text == "📋 Topshiriqlar")
async def list_tasks(message: Message):
    user = get_user(message.from_user.id)
    if not user or not user[7]:
        await message.answer("Avval ro‘yxatdan o‘ting va admin tasdig‘ini kuting.")
        return
    direction = user[5]
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""SELECT id, title, description, deadline FROM tasks
                      WHERE (direction = ? OR direction = 'Barchasi')
                      AND (region = ? OR region = 'Barchasi')
                      ORDER BY id DESC""", (direction, user[2]))
    tasks = cursor.fetchall()
    conn.close()

    if not tasks:
        await message.answer("Hozircha siz uchun aktiv topshiriqlar yo‘q.")
        return

    msg = "📋 **Sizning topshiriqlaringiz:**\n\n"
    for t in tasks:
        msg += f"🔹 **ID {t[0]}: {t[1]}**\n{t[2]}\n⏰ Muddat: {t[3]}\n\n"
    await message.answer(msg, parse_mode="Markdown")

@dp.message(F.text == "📤 Hisobot yuborish")
async def report_start(message: Message, state: FSMContext):
    await message.answer("Hisobot yubormoqchi bo‘lgan topshiriq ID’sini kiriting:")
    await state.set_state(ReportStates.task_id)

@dp.message(ReportStates.task_id)
async def report_task_id(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Iltimos, son kiriting.")
        return
    task_id = int(message.text)
    task = get_task(task_id)
    user = get_user(message.from_user.id)
    if not task or not user or not user[7]:
        await message.answer("Topshiriq topilmadi yoki sizning arizangiz tasdiqlanmagan.")
        await state.clear()
        return
    if task[3] not in (user[5], "Barchasi") or task[4] not in (user[2], "Barchasi"):
        await message.answer("Bu topshiriq sizning yo‘nalishingiz yoki hududingiz uchun emas.")
        await state.clear()
        return
    await state.update_data(task_id=task_id)
    await message.answer("Hisobotingizni matn, rasm yoki hujjat shaklida yuboring:")
    await state.set_state(ReportStates.content)

@dp.message(ReportStates.content)
async def report_content(message: Message, state: FSMContext):
    data = await state.get_data()
    user = get_user(message.from_user.id)
    if not user or not user[7]:
        await state.clear()
        await message.answer("Hisobot yuborish uchun admin tasdig‘i kerak.")
        return
    task = get_task(data.get('task_id'))
    if not task:
        await state.clear()
        await message.answer("Topshiriq topilmadi. Qaytadan boshlang.")
        return
    content_type = "text"
    content = message.text or ""
    if message.photo:
        content_type = "photo"
        content = message.photo[-1].file_id
    elif message.document:
        content_type = "document"
        content = message.document.file_id

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO reports (task_id, user_id, content_type, content, submitted_at)
    VALUES (?, ?, ?, ?, ?)
    """, (data['task_id'], message.from_user.id, content_type, content, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

    await state.clear()
    await message.answer("✅ Hisobotingiz yuborildi. Status: Baholash kutilmoqda")
    if OWNER_ID:
        try:
            await bot.send_message(OWNER_ID, f"📤 Yangi hisobot\nTopshiriq: #{data['task_id']}\nKoordinator: {user[1]} (ID {message.from_user.id})\nBaholash uchun botdagi ‘Hisobotlarni baholash’ bo‘limini oching.")
        except Exception:
            logger.exception("Hisobot haqida adminga xabar yuborilmadi")

# Admin Paneli
@dp.message(F.text == "🧭 Koordinator paneli")
async def admin_panel(message: Message):
    user = get_user(message.from_user.id)
    if not user or user[6] not in ['bosh_admin', 'viloyat_masuli', 'tuman_masuli']:
        await message.answer("Sizda ushbu bo‘lim uchun ruxsat yo‘q.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Topshiriq yaratish", callback_data="create_task")],
        [InlineKeyboardButton(text="📊 Hisobotlarni baholash", callback_data="review_reports")]
    ])
    await message.answer("🧭 **Boshqaruv paneli:**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "review_reports")
async def review_reports(call: CallbackQuery):
    user = get_user(call.from_user.id)
    if not user or user[6] not in ('bosh_admin', 'viloyat_masuli', 'tuman_masuli'):
        await call.answer("Ruxsat yo‘q.", show_alert=True)
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""SELECT reports.id, reports.task_id, reports.user_id, reports.content_type,
                      reports.content, tasks.title, users.full_name
                      FROM reports JOIN tasks ON tasks.id=reports.task_id
                      JOIN users ON users.user_id=reports.user_id
                      WHERE reports.status='Baholash kutilmoqda' ORDER BY reports.id DESC LIMIT 20""")
    reports = cursor.fetchall()
    conn.close()
    if not reports:
        await call.message.answer("Baholash kutilayotgan hisobot yo‘q.")
        await call.answer()
        return
    for report_id, task_id, user_id, kind, content, title, full_name in reports:
        await call.message.answer(
            f"Hisobot #{report_id} · Topshiriq #{task_id}: {title}\nKoordinator: {full_name} (ID {user_id})\n\n{content if kind == 'text' else 'Ilova qilingan fayl'}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{n} ball", callback_data=f"score_{report_id}_{n}") for n in range(1, 6)],
                [InlineKeyboardButton(text=f"{n} ball", callback_data=f"score_{report_id}_{n}") for n in range(6, 11)]
            ])
        )
        if kind == 'photo':
            await bot.send_photo(call.from_user.id, content)
        elif kind == 'document':
            await bot.send_document(call.from_user.id, content)
    await call.answer()

@dp.callback_query(F.data.startswith("score_"))
async def score_report(call: CallbackQuery):
    manager = get_user(call.from_user.id)
    if not manager or manager[6] not in ('bosh_admin', 'viloyat_masuli', 'tuman_masuli'):
        await call.answer("Baholash huquqingiz yo‘q.", show_alert=True)
        return
    _, report_id, score = call.data.split("_")
    report_id, score = int(report_id), int(score)
    if not 1 <= score <= 10:
        await call.answer("Ball 1 dan 10 gacha bo‘lishi kerak.", show_alert=True)
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""SELECT reports.user_id, reports.task_id, tasks.region
                      FROM reports JOIN tasks ON tasks.id=reports.task_id
                      WHERE reports.id=?""", (report_id,))
    record = cursor.fetchone()
    if record and manager[6] != 'bosh_admin' and record[2] != manager[2]:
        conn.close()
        await call.answer("Bu hududdagi hisobotni baholash vakolatingiz yo‘q.", show_alert=True)
        return
    if record:
        cursor.execute("UPDATE reports SET score=?, status=? WHERE id=?", (score, f"{score}/10 ball", report_id))
    conn.commit()
    conn.close()
    if not record:
        await call.answer("Hisobot topilmadi.", show_alert=True)
        return
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer("Ball saqlandi")
    try:
        await bot.send_message(record[0], f"Hisobotingiz #{record[1]} baholandi: {score}/10 ball.")
    except Exception:
        pass

@dp.callback_query(F.data == "create_task")
async def create_task_start(call: CallbackQuery, state: FSMContext):
    user = get_user(call.from_user.id)
    if not user or user[6] not in ('bosh_admin', 'viloyat_masuli', 'tuman_masuli'):
        await call.answer("Faqat mas’ullar topshiriq yaratishi mumkin.", show_alert=True)
        return
    dirs = DIRECTIONS + ["Barchasi"]
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=d)] for d in dirs], resize_keyboard=True, one_time_keyboard=True)
    await call.message.answer("Topshiriq yo‘nalishini tanlang:", reply_markup=kb)
    await state.set_state(TaskCreationStates.direction)
    await call.answer()

@dp.message(TaskCreationStates.direction)
async def task_dir(message: Message, state: FSMContext):
    user = get_user(message.from_user.id)
    if not user or user[6] not in ('bosh_admin', 'viloyat_masuli', 'tuman_masuli'):
        await state.clear()
        await message.answer("Faqat mas’ullar topshiriq yaratishi mumkin.")
        return
    if message.text not in DIRECTIONS and message.text != "Barchasi":
        await message.answer("Yo‘nalishni tugmalardan tanlang.")
        return
    if message.text not in DIRECTIONS and message.text != "Barchasi":
        await message.answer("Yo‘nalishni tugmalardan tanlang.")
        return
    await state.update_data(direction=message.text)
    await message.answer("Hudud (Viloyat/Tuman yoki 'Barchasi') ni kiriting:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(TaskCreationStates.region)

@dp.message(TaskCreationStates.region)
async def task_reg(message: Message, state: FSMContext):
    user = get_user(message.from_user.id)
    if not user or user[6] not in ('bosh_admin', 'viloyat_masuli', 'tuman_masuli'):
        await state.clear()
        await message.answer("Topshiriq yaratish huquqingiz yo‘q.")
        return
    if message.text != "Barchasi" and message.text not in REGIONS:
        await message.answer("Viloyat nomini to‘g‘ri kiriting yoki ‘Barchasi’ deb yozing.")
        return
    if user[6] != 'bosh_admin' and message.text not in (user[2], 'Barchasi'):
        await message.answer("Siz faqat o‘z viloyatingizga topshiriq bera olasiz.")
        return
    if user[6] != 'bosh_admin' and message.text == 'Barchasi':
        await message.answer("Umumiy topshiriqni faqat bosh administrator beradi.")
        return
    await state.update_data(region=message.text)
    await message.answer("Topshiriq sarlavhasini kiriting:")
    await state.set_state(TaskCreationStates.title)

@dp.message(TaskCreationStates.title)
async def task_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("Topshiriq tavsifini kiriting:")
    await state.set_state(TaskCreationStates.description)

@dp.message(TaskCreationStates.description)
async def task_desc(message: Message, state: FSMContext):
    if not message.text or len(message.text) > 3000:
        await message.answer("Tavsif 1–3000 belgi oralig‘ida bo‘lsin.")
        return
    await state.update_data(description=message.text)
    await message.answer("Topshiriq muddatini kiriting (masalan: 2026-12-31):")
    await state.set_state(TaskCreationStates.deadline)

@dp.message(TaskCreationStates.deadline)
async def task_deadline(message: Message, state: FSMContext):
    data = await state.get_data()
    if not message.text or len(data.get('title', '')) > 150 or not data.get('title'):
        await message.answer("Sarlavha bo‘sh bo‘lmasin va 150 belgidan oshmasin.")
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        datetime.strptime(message.text.strip(), "%Y-%m-%d")
    except ValueError:
        await message.answer("Muddatni YYYY-MM-DD ko‘rinishida kiriting. Masalan: 2026-12-31")
        return
    cursor.execute("""
    INSERT INTO tasks (title, description, direction, region, deadline, created_by, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (data['title'], data['description'], data['direction'], data['region'], message.text, message.from_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("✅ Topshiriq muvaffaqiyatli yaratildi!", reply_markup=main_menu_keyboard('bosh_admin', True))

# Web Server (aiohttp) Integratsiyasi
async def handle_root(request):
    return web.Response(text="Bot runs OK", status=200)

async def handle_health(request):
    return web.json_response({"status": "ok", "bot": "polling"})

async def main():
    # Web server
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_health)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

    # Start bot long-polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

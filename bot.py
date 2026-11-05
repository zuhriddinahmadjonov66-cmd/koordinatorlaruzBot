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

# Logging sozlamalari (Maxfiy ma'lumotlar logga chiqmaydi)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Konfiguratsiya va Envlarni yuklash
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
PROMO_CODE = os.getenv("PROMO_CODE", "PROMO2026")
ADMIN_CONTACT = os.getenv("ADMIN_CONTACT", "@admin")
PORT = int(os.getenv("PORT", "8080"))

# JSON fayllarni o'qish
def load_json(filepath: str) -> dict:
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

config_data = load_json("config.json")
questions_data = load_json("questions.json").get("questions", [])

BOT_NAME = config_data.get("bot_name", "Koordinatorlar platformasi")
DIRECTIONS = config_data.get("directions", [])

# Database boshqaruvi
DB_PATH = "bot_database.db"

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
    
    conn.commit()
    conn.close()

init_db()

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

def register_user(user_id, full_name, region, district, school, direction, referrer_id=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    role = 'bosh_admin' if user_id == OWNER_ID else 'koordinator'
    is_approved = 1 if user_id == OWNER_ID else 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute("""
    INSERT OR REPLACE INTO users (user_id, full_name, region, district, school, direction, role, is_approved, referrer_id, registered_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, full_name, region, district, school, direction, role, is_approved, referrer_id, now))
    
    conn.commit()
    conn.close()

# Bot Handlerlari
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user = get_user(message.from_user.id)
    args = message.text.split()
    referrer_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None

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
    await message.answer("Yordam bo‘limi:\n\nBotdan foydalanish uchun ro‘yxatdan o‘ting va administrator tasdig‘ini kuting.")

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
    user_id = int(call.data.split("_")[1])
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_approved = 1 WHERE user_id = ?", (user_id,))
    cursor.execute("SELECT referrer_id FROM users WHERE user_id = ?", (user_id,))
    ref = cursor.fetchone()
    if ref and ref[0]:
        referrer_id = ref[0]
        cursor.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?", (referrer_id,))
        cursor.execute("SELECT referral_count FROM users WHERE user_id = ?", (referrer_id,))
        c = cursor.fetchone()[0]
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
    try:
        await bot.send_message(user_id, "✅ Sizning akkauntingiz admin tomonidan tasdiqlandi!")
    except Exception:
        pass

# Menyu Tugmalari Handlerlari
@dp.message(F.text == "👤 Profilim")
async def profile_handler(message: Message):
    user = get_user(message.from_user.id)
    if not user:
        await message.answer("Siz ro‘yxatdan o‘tmagansiz. /start bosing.")
        return
    pro_status = "Aktiv ⭐" if user[8] else "Noaktiv"
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
    if user and user[8]:
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
    text = "📸 **Media qo‘llanma:**\n1. Rasm sifatli va yorug‘ joyda olingan bo‘lishi kerak.\n2. Hisobot ob’ekti aniq aks etishi lozim.\n3. Hujjatlar ko‘rinarli skanerlangan bo‘lishi shart."
    await call.message.answer(text, parse_mode="Markdown")

@dp.callback_query(F.data == "pro_lessons")
async def pro_lessons_cb(call: CallbackQuery):
    text = "📘 **Koordinator darsligi:**\n1. Reja tuzish va vaqtni boshqarish.\n2. Jamoani shakllantirish va vazifalar taqsimoti.\n3. Natijador hisobot tayyorlash va tahlil."
    await call.message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🎟 Promo kod")
async def promo_start(message: Message, state: FSMContext):
    await message.answer("Iltimos, promo kodni kiriting:")
    await state.set_state(PromoStates.code)

@dp.message(PromoStates.code)
async def promo_process(message: Message, state: FSMContext):
    user = get_user(message.from_user.id)
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
    cursor.execute("""
    INSERT INTO dls_registrations (user_id, full_name, dls_nickname, phone, registered_at)
    VALUES (?, ?, ?, ?, ?)
    """, (message.from_user.id, data['full_name'], data['dls_nickname'], phone, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    await state.clear()
    await message.answer("✅ DLS Turniriga muvaffaqiyatli yozildingiz!\n🗓 Turnir sanasi: **Tez kunda**", parse_mode="Markdown")

# Quiz Handlerlari
@dp.callback_query(F.data == "quiz_start")
async def quiz_start(call: CallbackQuery, state: FSMContext):
    if not questions_data:
        await call.message.answer("Savollar bazasi bo‘sh.")
        return
    await state.update_data(current_index=0, correct_answers=0)
    await send_quiz_question(call.message, 0, state)

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

# Topshiriqlar va Hisobotlar
@dp.message(F.text == "📋 Topshiriqlar")
async def list_tasks(message: Message):
    user = get_user(message.from_user.id)
    if not user:
        return
    direction = user[5]
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, description, deadline FROM tasks WHERE direction = ? OR direction = 'Barchasi'", (direction,))
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
    await state.update_data(task_id=int(message.text))
    await message.answer("Hisobotingizni matn, rasm yoki hujjat shaklida yuboring:")
    await state.set_state(ReportStates.content)

@dp.message(ReportStates.content)
async def report_content(message: Message, state: FSMContext):
    data = await state.get_data()
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

@dp.callback_query(F.data == "create_task")
async def create_task_start(call: CallbackQuery, state: FSMContext):
    dirs = DIRECTIONS + ["Barchasi"]
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=d)] for d in dirs], resize_keyboard=True, one_time_keyboard=True)
    await call.message.answer("Topshiriq yo‘nalishini tanlang:", reply_markup=kb)
    await state.set_state(TaskCreationStates.direction)

@dp.message(TaskCreationStates.direction)
async def task_dir(message: Message, state: FSMContext):
    await state.update_data(direction=message.text)
    await message.answer("Hudud (Viloyat/Tuman yoki 'Barchasi') ni kiriting:", reply_markup=ReplyKeyboardRemove())
    await state.set_state(TaskCreationStates.region)

@dp.message(TaskCreationStates.region)
async def task_reg(message: Message, state: FSMContext):
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
    await state.update_data(description=message.text)
    await message.answer("Topshiriq muddatini kiriting (masalan: 2026-12-31):")
    await state.set_state(TaskCreationStates.deadline)

@dp.message(TaskCreationStates.deadline)
async def task_deadline(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
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
    return web.json_response({"status": "ok", "bot": "running"})

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

"""Koordinatorlar platformasi Telegram bot.

Render-da ishlaydi. Polling + aiohttp web server birga ishlaydi.
"""

import asyncio
import json
import os
import signal
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=5))

REGIONS = [
    "Andijon",
    "Buxoro",
    "Farg'ona",
    "Jizzax",
    "Namangan",
    "Navoiy",
    "Qashqadaryo",
    "Qoraqalpog'iston Respublikasi",
    "Samarqand",
    "Sirdaryo",
    "Surxondaryo",
    "Toshkent viloyati",
    "Toshkent shahri",
]

DIRECTIONS = [
    "💻 IT va raqamli texnologiyalar",
    "📚 Ta’lim va ilm-fan",
    "🎨 Madaniyat va san’at",
    "⚽ Sport va sog‘lom turmush tarzi",
    "🌱 Ekologiya va atrof-muhit muhofazasi",
    "🤝 Volontyorlik va ijtimoiy loyihalar",
    "🚀 Tadbirkorlik va innovatsiyalar",
    "⚖️ Huquqiy savodxonlik va fuqarolik tashabbuslari",
]

MAIN_MENU = [
    ["🧭 Koordinator paneli", "📋 Topshiriqlar"],
    ["📤 Hisobot yuborish", "📖 Koordinator nima?"],
    ["⭐ PRO obuna", "🎟 Promo kod"],
    ["🏆 Turnir va o‘yinlar", "🆘 Adminga murojaat"],
    ["ℹ️ Bot haqida", "👤 Profilim"],
]

PRO_MENU = [
    ["📸 Media qo‘llanma", "📘 Koordinator darsligi"],
    ["📋 Topshiriqlar", "📤 Hisobot yuborish"],
    ["👤 Profilim", "🆘 Adminga murojaat"],
]

TASK_STATUSES = [
    "yangi",
    "bajarilmoqda",
    "hisobot yuborilgan",
    "qabul qilingan",
    "tuzatishga qaytarilgan",
    "baholash kutilmoqda",
]


def load_env():
    dotenv_path = ROOT / ".env"
    if dotenv_path.exists():
        for line in dotenv_path.read_text(encoding="utf-8-sig").splitlines():
            if not line or line.strip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class Telegram:
    def __init__(self, token):
        self.base = "https://api.telegram.org/bot" + token + "/"

    def call(self, method, **data):
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            self.base + method,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Telegram HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError("Telegram bilan aloqa yo‘q") from None
        if not result.get("ok"):
            raise RuntimeError("Telegram so‘rovni qabul qilmadi")
        return result["result"]


class App:
    def __init__(self, db_path, api, owner_id, directions):
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.api = api
        self.owner = owner_id
        self.directions = directions
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY,
                name TEXT,
                region TEXT,
                district TEXT,
                school TEXT,
                direction TEXT,
                role TEXT DEFAULT 'member',
                approved INTEGER DEFAULT 0,
                phone TEXT,
                pro_active INTEGER DEFAULT 0,
                pro_expires_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                invite_count INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sessions(
                id INTEGER PRIMARY KEY,
                kind TEXT,
                data TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator INTEGER,
                title TEXT,
                due TEXT,
                region TEXT,
                district TEXT,
                direction TEXT,
                status TEXT DEFAULT 'yangi',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS reports(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                user_id INTEGER,
                direction TEXT,
                body TEXT,
                file_id TEXT,
                file_kind TEXT,
                status TEXT DEFAULT 'baholash kutilmoqda',
                score INTEGER,
                note TEXT,
                reviewer_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(task_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS suggestions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid INTEGER,
                body TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS promo_usage(
                code TEXT,
                user_id INTEGER,
                used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(code, user_id)
            );
            CREATE TABLE IF NOT EXISTS admin_messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                status TEXT DEFAULT 'yangi',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS data_delete_requests(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                status TEXT DEFAULT 'kutilmoqda',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS meta(
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS reminders(
                task_id INTEGER,
                user_id INTEGER,
                PRIMARY KEY(task_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS quiz_results(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                full_name TEXT,
                telegram_id INTEGER,
                score INTEGER,
                total INTEGER,
                percent INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                best_score INTEGER,
                best_percent INTEGER,
                attempts INTEGER DEFAULT 1
            );
            """
        )
        self.ensure_admin()

    def ensure_admin(self):
        row = self.one("SELECT id FROM users WHERE id=?", (self.owner,))
        if row is None:
            self.write(
                "INSERT INTO users(id, name, role, approved, region, district, school, direction) VALUES(?,?,?,?,?,?,?,?)",
                (self.owner, "Bosh administrator", "owner", 1, "", "", "", ""),
            )
        else:
            self.write(
                "UPDATE users SET name=COALESCE(name,'Bosh administrator'), role='owner', approved=1 WHERE id=?",
                (self.owner,),
            )

    def one(self, sql, args=()):
        return self.db.execute(sql, args).fetchone()

    def rows(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def write(self, sql, args=()):
        cur = self.db.execute(sql, args)
        self.db.commit()
        return cur.lastrowid

    def send(self, uid, text, keyboard=None, disable_web_page_preview=True):
        kb = keyboard if keyboard is not None else MAIN_MENU
        try:
            for start in range(0, len(text), 3800):
                self.api.call(
                    "sendMessage",
                    chat_id=uid,
                    text=text[start : start + 3800],
                    reply_markup={"keyboard": kb, "resize_keyboard": True},
                    disable_web_page_preview=disable_web_page_preview,
                )
        except RuntimeError:
            pass

    def notify(self, uid, text):
        if uid is None:
            return
        try:
            self.send(uid, text)
        except Exception:
            pass

    def user(self, uid):
        return self.one("SELECT * FROM users WHERE id=?", (uid,))

    def session(self, uid, kind, payload):
        self.write(
            "INSERT OR REPLACE INTO sessions(id, kind, data) VALUES(?,?,?)",
            (uid, kind, json.dumps(payload, ensure_ascii=False)),
        )

    def clear_session(self, uid):
        self.write("DELETE FROM sessions WHERE id=?", (uid,))

    def get_session(self, uid):
        row = self.one("SELECT * FROM sessions WHERE id=?", (uid,))
        if row is None:
            return None
        try:
            return row["kind"], json.loads(row["data"] or "{}")
        except Exception:
            return row["kind"], {}

    def manager(self, u):
        return bool(u) and u["approved"] and u["role"] in ("owner", "region", "district")

    def owner_only(self, u):
        return bool(u) and u["approved"] and u["role"] == "owner"

    def has_pro(self, uid):
        u = self.user(uid)
        if not u or not u["pro_active"] or not u["pro_expires_at"]:
            return False
        try:
            expires = datetime.strptime(u["pro_expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
            return datetime.now(TZ) < expires
        except Exception:
            return False

    def visible_task(self, u, task):
        if not u or not u["approved"] or task is None:
            return False
        if u["role"] == "owner":
            return True
        if task["region"] and task["region"] != u["region"]:
            return False
        if task["district"] and task["district"] != u["district"]:
            return False
        if task["direction"] and task["direction"] != u["direction"]:
            return False
        return True

    def reviewable_task(self, u, task):
        if not self.manager(u) or task is None:
            return False
        if u["role"] == "owner":
            return True
        if task["region"] != u["region"]:
            return False
        if u["role"] == "region":
            return True
        if u["role"] == "district":
            return task["district"] == u["district"]
        return False

    def profile_text(self, u):
        if not u:
            return "Foydalanuvchi topilmadi."
        return "\n".join(
            [
                f"👤 Ism: {u['name'] or 'Noma’lum'}",
                f"🆔 Telegram ID: {u['id']}",
                f"📍 Hudud: {u['region'] or 'Ko‘rsatilmagan'}",
                f"🏙 Tuman: {u['district'] or 'Ko‘rsatilmagan'}",
                f"🏫 Maktab: {u['school'] or 'Ko‘rsatilmagan'}",
                f"🧭 Yo‘nalish: {u['direction'] or 'Ko‘rsatilmagan'}",
                f"👑 Rol: {u['role']}",
                f"📌 Tasdiqlangan: {'Ha' if u['approved'] else 'Yo‘q'}",
                f"⭐ PRO: {'Faol' if self.has_pro(u['id']) else 'Yoq'}",
                f"📆 PRO tugash sanasi: {u['pro_expires_at'] or 'Yo‘q'}",
            ]
        )

    def main_keyboard(self, uid):
        if self.has_pro(uid):
            return PRO_MENU
        return MAIN_MENU

    def handle(self, message):
        if message.get("chat", {}).get("type") != "private":
            return

        uid = int(message.get("from", {}).get("id"))
        text = (message.get("text") or "").strip()
        cmd = text.split()[0].split("@") [0] if text else ""
        u = self.user(uid)

        # Global commands
        if cmd == "/id":
            self.send(uid, f"Telegram ID: {uid}")
            return
        if text in ("❌ Bekor qilish", "/cancel"):
            self.clear_session(uid)
            self.send(uid, "Bekor qilindi.")
            return
        if cmd == "/help":
            self.send(
                uid,
                "\n".join(
                    [
                        "Botdan foydalanish bo‘yicha ko‘rsatma:",
                        "/start — bosh menyu",
                        "/cancel — bekor qilish",
                        "/id — Telegram ID",
                        "/report 12 — hisobot yuborish",
                        "/promo KOD — promo kod",
                        "/newtask — topshiriq yaratish",
                        "/delete_data — ma’lumotlarni o‘chirish so‘rovi",
                        "/help — yordam",
                    ]
                ),
            )
            return
        if cmd == "/delete_data":
            self.write("INSERT OR IGNORE INTO data_delete_requests(user_id) VALUES(?)", (uid,))
            self.notify(self.owner, f"Ma’lumotlarni o‘chirish so‘rovi: {uid}")
            self.send(uid, "So‘rov yuborildi. Admin tasdiqlagandan keyin ma’lumotlaringiz o‘chiriladi.")
            return
        if cmd == "/promo":
            code = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else ""
            self.process_promo(uid, code)
            return

        if cmd == "/start":
            self.clear_session(uid)
            if u is None:
                self.session(uid, "register_name", {})
                self.send(uid, "Assalomu alaykum! Botga ro‘yxatdan o‘tish uchun ismingizni kiriting:")
                return
            if not u["approved"]:
                self.send(uid, "Arizangiz tasdiqlanishini kutyapti. Admin roziligini kuting.")
                return
            self.send(uid, "Bosh menyu", self.main_keyboard(uid))
            return

        if u is None:
            self.send(uid, "Avval /start bosing.")
            return

        if not u["approved"]:
            self.send(uid, "Arizangiz hali tasdiqlanmagan. Admin tasdig‘i kutilmoqda.")
            return

        # Registration flow
        session = self.get_session(uid)
        if session is not None:
            kind, payload = session
            if kind == "register_name":
                name = text.strip()
                if not name or len(name) > 80:
                    self.send(uid, "Iltimos, ismingizni 1–80 belgidan iborat qilib kiriting.")
                    return
                self.write("UPDATE users SET name=? WHERE id=?", (name, uid))
                self.session(uid, "register_region", {"name": name})
                self.send(uid, "Hududingizni tanlang:", [[r] for r in REGIONS])
                return
            if kind == "register_region":
                region = text.strip()
                if region not in REGIONS:
                    self.send(uid, "Hududni ro‘yxatdan tanlang.", [[r] for r in REGIONS])
                    return
                self.write("UPDATE users SET region=? WHERE id=?", (region, uid))
                self.session(uid, "register_district", {"region": region})
                self.send(uid, "Tuman yoki shahar nomini kiriting:")
                return
            if kind == "register_district":
                district = text.strip()
                if not district or len(district) > 80:
                    self.send(uid, "Tuman yoki shahar nomini to‘g‘ri kiriting.")
                    return
                self.write("UPDATE users SET district=? WHERE id=?", (district, uid))
                self.session(uid, "register_school", {})
                self.send(uid, "Maktab yoki tashkilot nomini kiriting:")
                return
            if kind == "register_school":
                school = text.strip()
                if not school or len(school) > 150:
                    self.send(uid, "Maktab nomini 1–150 belgi oralig‘ida kiriting.")
                    return
                self.write("UPDATE users SET school=? WHERE id=?", (school, uid))
                self.session(uid, "register_direction", {})
                self.send(uid, "Yo‘nalishingizni tanlang:", [[d] for d in DIRECTIONS])
                return
            if kind == "register_direction":
                direction = text.strip()
                if direction not in DIRECTIONS:
                    self.send(uid, "Yo‘nalishni ro‘yxatdan tanlang.", [[d] for d in DIRECTIONS])
                    return
                self.write("UPDATE users SET direction=? WHERE id=?", (direction, uid))
                self.clear_session(uid)
                self.send(uid, "Ro‘yxatdan o‘tishingiz yakunlandi. Admin tasdiqlashi kutilmoqda.")
                self.notify(self.owner, f"Yangi foydalanuvchi: {uid} | {self.user(uid)['name']} | {self.user(uid)['region']} | {direction}")
                return
            if kind == "report":
                task_id = safe_int(payload.get("task_id"), 0)
                task = self.one("SELECT * FROM tasks WHERE id=?", (task_id,))
                if task is None or not self.visible_task(u, task):
                    self.clear_session(uid)
                    self.send(uid, "Bu topshiriq sizga tegishli emas.")
                    return
                body = text.strip() if text else ""
                file_id = ""
                file_kind = ""
                if message.get("photo"):
                    file_id = message["photo"][-1]["file_id"]
                    file_kind = "photo"
                elif message.get("document"):
                    file_id = message["document"]["file_id"]
                    file_kind = "document"
                if not body and not file_id:
                    self.send(uid, "Hisobotning matni yoki fayli bo‘lishi kerak.")
                    return
                self.write(
                    "INSERT INTO reports(task_id, user_id, direction, body, file_id, file_kind, status) VALUES(?,?,?,?,?,?,?) "
                    "ON CONFLICT(task_id, user_id) DO UPDATE SET body=excluded.body, file_id=excluded.file_id, file_kind=excluded.file_kind, direction=excluded.direction, status='hisobot yuborilgan'",
                    (task_id, uid, task["direction"], body or message.get("caption", ""), file_id, file_kind, "hisobot yuborilgan"),
                )
                self.clear_session(uid)
                self.send(uid, "Hisobot yuborildi. Admin tekshiruvdan o‘tib, baholashni amalga oshiradi.")
                self.notify(task["creator"], f"#{task_id} topshiriq uchun yangi hisobot yuborildi.")
                return
            if kind == "support":
                msg = text.strip()
                if not msg or len(msg) > 3000:
                    self.send(uid, "Murojaat matni 1–3000 belgidan iborat bo‘lishi kerak.")
                    return
                self.write("INSERT INTO admin_messages(user_id, text, status) VALUES(?,?,?)", (uid, msg, "yangi"))
                self.clear_session(uid)
                self.send(uid, "Murojaatingiz adminga yuborildi.")
                self.notify(self.owner, f"Yangi murojaat: {uid}\n{msg}")
                return
            if kind == "promo_code":
                self.process_promo(uid, text.strip())
                self.clear_session(uid)
                return
            if kind == "dls_register":
                items = [p.strip() for p in text.split("|")]
                if len(items) != 3:
                    self.send(uid, "Formati noto‘g‘ri. Ism | DLS akkaunt nomi | Telefon raqami ko‘rinishida kiriting.")
                    return
                name, dls_name, phone = items
                self.write(
                    "INSERT INTO admin_messages(user_id, text, status) VALUES(?,?,?)",
                    (uid, f"DLS turniriga yozilish: {name} | {dls_name} | {phone}", "yangi"),
                )
                self.clear_session(uid)
                self.send(uid, "Arizangiz turnir tashkilotchisiga yuborildi.")
                return
            if kind == "task_create":
                parts = [p.strip() for p in text.split("|")]
                if len(parts) < 5:
                    self.send(uid, "Noto‘g‘ri format. Misol: Sarlavha | 2026-12-01 18:00 | * | * | 💻 IT va raqamli texnologiyalar")
                    return
                title, due, region, district, direction = parts[:5]
                try:
                    datetime.strptime(due, "%Y-%m-%d %H:%M")
                except ValueError:
                    self.send(uid, "Sana formati noto‘g‘ri. Misol: 2026-12-01 18:00")
                    return
                if region == "*":
                    region = ""
                if district == "*":
                    district = ""
                if direction == "*":
                    direction = ""
                if region and region not in REGIONS:
                    self.send(uid, "Viloyat nomi noto‘g‘ri.")
                    return
                if direction and direction not in DIRECTIONS:
                    self.send(uid, "Yo‘nalish nomi noto‘g‘ri.")
                    return
                if not self.manager(u):
                    self.clear_session(uid)
                    self.send(uid, "Faqat mas’ullar topshiriq yaratishi mumkin.")
                    return
                tid = self.write(
                    "INSERT INTO tasks(creator, title, due, region, district, direction, status) VALUES(?,?,?,?,?,?,?)",
                    (uid, title, due, region, district, direction, "yangi"),
                )
                self.clear_session(uid)
                self.send(uid, f"Topshiriq #{tid} saqlandi.")
                return
            if kind == "quiz":
                if text.strip().upper() in {"A", "B", "C", "D"}:
                    self.handle_quiz_step(uid, text.strip().upper())
                    return

        # Main menu handlers
        if text == "🧭 Koordinator paneli":
            self.send(
                uid,
                "Koordinator paneli:\n- topshiriq yaratish\n- hisobotlarni ko‘rish\n- baholash\n- umumiy reyting",
            )
            return

        if text == "📋 Topshiriqlar":
            items = []
            for task in self.rows("SELECT * FROM tasks ORDER BY id DESC"):
                if not self.visible_task(u, task):
                    continue
                report = self.one("SELECT * FROM reports WHERE task_id=? AND user_id=?", (task["id"], uid))
                status = report["status"] if report else "yuborilmagan"
                items.append(f"#{task['id']} — {task['title']}\nMuddat: {task['due'] or 'Belgilanmagan'}\nYo‘nalish: {task['direction'] or 'Barchasi'}\nHolat: {status}\n/report {task['id']}")
            self.send(uid, "\n\n".join(items) if items else "Sizga tegishli topshiriqlar yo‘q.")
            return

        if text == "📤 Hisobot yuborish":
            self.send(uid, "Topshiriq raqamini kiriting: /report 12")
            return

        if cmd == "/report":
            parts = text.split()
            if len(parts) < 2:
                self.send(uid, "Masalan: /report 12")
                return
            task_id = safe_int(parts[1], 0)
            task = self.one("SELECT * FROM tasks WHERE id=?", (task_id,))
            if task is None or not self.visible_task(u, task):
                self.send(uid, "Bu topshiriq sizga tegishli emas yoki mavjud emas.")
                return
            self.session(uid, "report", {"task_id": task_id})
            self.send(uid, "Hisobot matnini yozing. So‘ngra rasm yoki hujjat biriktirishingiz mumkin.")
            return

        if text == "📖 Koordinator nima?":
            self.send(
                uid,
                "Koordinator — hudud yoki jamoa faoliyatini muvofiqlashtiruvchi shaxsdir. U topshiriqlarni tayinlaydi, natijalarni kuzatadi va hisobotlarni tekshiradi.\n\nRasmiy manbada aniq sanalar aniqlanmadi. Bu bo‘limdagi ma’lumot admin tomonidan keyin to‘ldiriladi."
            )
            return

        if text == "⭐ PRO obuna":
            self.send(
                uid,
                "PRO obuna haqida ma’lumot:\n- Narxi: 10 000 so‘m / 1 oy\n- Amal qilish muddati: 1 oy\n- Funksiyalar: media qo‘llanma va koordinator darsligi\n- To‘lov tizimi hozircha ulanmagan. Foydalanuvchi admin bilan bog‘lanishi kerak.\n\nAdmin to‘lovni tekshirib, PRO muddatini qo‘lda faollashtiradi.",
            )
            return

        if text == "🎟 Promo kod":
            self.session(uid, "promo_code", {})
            self.send(uid, "Promo kodni kiriting:")
            return

        if text == "🏆 Turnir va o‘yinlar":
            self.send(
                uid,
                "Turnir va o‘yinlar bo‘limi:\n1) DLS turniri\n2) Dunyoviy bilim testi\n3) 5 ta odam taklif qil — promo kod",
            )
            return

        if text == "DLS turniri":
            self.session(uid, "dls_register", {})
            self.send(uid, "Iltimos, quyidagi formatda kiriting: Ism | DLS akkaunt nomi | Telefon raqami\nTelefon raqami faqat turnir tashkilotchisiga yuboriladi.")
            return

        if text == "Dunyoviy bilim testi":
            self.start_quiz(uid)
            return

        if cmd == "/quiz":
            self.start_quiz(uid)
            return

        if text == "5 ta odam taklif qil — promo kod":
            self.send(
                uid,
                "Taklif qoidalari:\n- Faqat yangi foydalanuvchi botni ishga tushirib, ro‘yxatdan o‘tib tasdiqlanganda hisobga olinadi.\n- Takroriy takliflar sanalmaydi.\n- Bir martalik bonus yoki promo kod admin tasdiqlagandan keyin beriladi.",
            )
            return

        if text == "🆘 Adminga murojaat":
            self.session(uid, "support", {})
            profile_url = os.environ.get("ADMIN_PROFILE_URL", "").strip()
            if profile_url:
                self.send(uid, f"Admin profil havolasi: {profile_url}\nSavolingizni, taklifingizni yoki xatosini yuboring.")
            else:
                self.send(uid, "Admin profil havolasi sozlanmagan. Murojaatingizni quyida yozing; admin keyin ko‘rib chiqadi.")
            return

        if text == "ℹ️ Bot haqida":
            self.send(
                uid,
                "Bot yaratuvchisi: Zuhriddin Ahmadjonov\n\nBu bot topshiriqlar, hisobotlar, reyting, PRO obuna, turnir va bilim testini boshqarish uchun mo‘ljallangan.\n\nFoydalanuvchi Telegram ID, telefon raqami va topshiriq hisobotlari ommaviy reytingda yoki boshqa foydalanuvchilarga ko‘rsatilmaydi.",
            )
            return

        if text == "👤 Profilim":
            self.send(uid, self.profile_text(u), self.main_keyboard(uid))
            return

        if text == "📸 Media qo‘llanma" and self.has_pro(uid):
            self.send(
                uid,
                "Media qo‘llanma:\n1) Rasmga aniq va yengil ko‘rinishni tanlang.\n2) Yorug‘likni tekshiring.\n3) Matn qisqa bo‘lsin.\n4) Rasm xavfsizlik va maxfiylik qoidalariga rioya qilsin.\n5) Format va hajmni nazorat qiling.",
            )
            return

        if text == "📘 Koordinator darsligi" and self.has_pro(uid):
            self.send(
                uid,
                "Koordinator darsligi:\n- rejalashtirish\n- jamoa bilan ishlash\n- topshiriq bajarish\n- hisobot tayyorlash\n- natijalarni tahlil qilish",
            )
            return

        if text == "📸 Media qo‘llanma" and not self.has_pro(uid):
            self.send(uid, "Bu menyu faqat PRO foydalanuvchilar uchun ochiq. PRO obunasini olish uchun "/start" dan "⭐ PRO obuna" ni tanlang.")
            return

        if text == "📘 Koordinator darsligi" and not self.has_pro(uid):
            self.send(uid, "Bu menyu faqat PRO foydalanuvchilar uchun ochiq. PRO obunasini olish uchun "/start" dan "⭐ PRO obuna" ni tanlang.")
            return

        if cmd == "/newtask":
            if not self.manager(u):
                self.send(uid, "Faqat mas’ul yoki admin topshiriq yaratishi mumkin.")
                return
            self.session(uid, "task_create", {})
            self.send(uid, "Topshiriq ma’lumotlarini quyidagi formatda yuboring: Sarlavha | 2026-12-01 18:00 | * | * | 💻 IT va raqamli texnologiyalar")
            return

        if cmd == "/reports":
            if not self.manager(u):
                self.send(uid, "Sizga bu bo‘limda vakolat yo‘q.")
                return
            out = []
            for task in self.rows("SELECT * FROM tasks ORDER BY id DESC"):
                if not self.reviewable_task(u, task):
                    continue
                reports = self.rows("SELECT * FROM reports WHERE task_id=? ORDER BY created_at DESC", (task["id"],))
                for r in reports:
                    score = r["score"] if r["score"] is not None else "baholash kutilmoqda"
                    out.append(f"#{task['id']} | user {r['user_id']} | status: {r['status']} | ball: {score}\n{r['body']}\n/rate {task['id']} {r['user_id']} 8 \"izoh\"\n/accept {task['id']} {r['user_id']}\n/return {task['id']} {r['user_id']} \"sabab\"")
            self.send(uid, "\n\n".join(out) if out else "Hisobotlar yo‘q.")
            return

        if cmd == "/accept":
            if not self.manager(u):
                self.send(uid, "Sizga bu amal uchun vakolat yo‘q.")
                return
            parts = text.split()
            if len(parts) < 3:
                self.send(uid, "Misol: /accept 12 345")
                return
            task_id = safe_int(parts[1], 0)
            report_user = safe_int(parts[2], 0)
            task = self.one("SELECT * FROM tasks WHERE id=?", (task_id,))
            if task is None or not self.reviewable_task(u, task):
                self.send(uid, "Siz ushbu topshiriq uchun baholashga vakolatli emassiz.")
                return
            self.write(
                "UPDATE reports SET status='qabul qilingan', reviewer_id=?, note=COALESCE(note,'') WHERE task_id=? AND user_id=?",
                (uid, task_id, report_user),
            )
            self.send(uid, "Hisobot qabul qilindi.")
            self.notify(report_user, f"#{task_id} topshiriq bo‘yicha hisobotingiz qabul qilindi.")
            return

        if cmd == "/return":
            if not self.manager(u):
                self.send(uid, "Sizga bu amal uchun vakolat yo‘q.")
                return
            parts = text.split(maxsplit=3)
            if len(parts) < 4:
                self.send(uid, "Misol: /return 12 345 \"Tuzatish kerak\"")
                return
            task_id = safe_int(parts[1], 0)
            report_user = safe_int(parts[2], 0)
            note = parts[3].strip()
            task = self.one("SELECT * FROM tasks WHERE id=?", (task_id,))
            if task is None or not self.reviewable_task(u, task):
                self.send(uid, "Siz ushbu topshiriq uchun baholashga vakolatli emassiz.")
                return
            self.write(
                "UPDATE reports SET status='tuzatishga qaytarilgan', reviewer_id=?, note=? WHERE task_id=? AND user_id=?",
                (uid, note, task_id, report_user),
            )
            self.send(uid, "Hisobot tuzatishga qaytarildi.")
            self.notify(report_user, f"#{task_id} topshiriq bo‘yicha hisobot tuzatishga qaytarildi. Sabab: {note}")
            return

        if cmd == "/rate":
            if not self.manager(u):
                self.send(uid, "Faqat vakolatli mas’ul ball bera oladi.")
                return
            parts = text.split(maxsplit=4)
            if len(parts) < 5:
                self.send(uid, "Misol: /rate 12 345 8 \"Yaxshi ishladi\"")
                return
            task_id = safe_int(parts[1], 0)
            report_user = safe_int(parts[2], 0)
            score = safe_int(parts[3], 0)
            note = parts[4].strip()
            if not (1 <= score <= 10):
                self.send(uid, "Ball 1 dan 10 gacha bo‘lishi kerak.")
                return
            task = self.one("SELECT * FROM tasks WHERE id=?", (task_id,))
            if task is None or not self.reviewable_task(u, task):
                self.send(uid, "Siz ushbu topshiriq uchun baholashga vakolatli emassiz.")
                return
            self.write(
                "UPDATE reports SET score=?, note=?, reviewer_id=?, status='qabul qilingan' WHERE task_id=? AND user_id=?",
                (score, note, uid, task_id, report_user),
            )
            self.send(uid, "Baholash saqlandi.")
            self.notify(report_user, f"#{task_id} topshiriq uchun bahoyingiz {score}/10 bo‘ldi. Izoh: {note}")
            return

        if cmd == "/ratings":
            overall = self.one("SELECT AVG(score) AS avg_score FROM reports WHERE score IS NOT NULL")
            lines = [
                f"Umumiy reyting: {overall['avg_score'] if overall and overall['avg_score'] is not None else 'Hali baho yo‘q'} / 10"
            ]
            for row in self.rows(
                "SELECT direction, AVG(score) AS avg_score FROM reports WHERE score IS NOT NULL GROUP BY direction ORDER BY avg_score DESC"
            ):
                lines.append(f"{row['direction']}: {row['avg_score']:.2f} / 10")
            self.send(uid, "\n".join(lines) if lines else "Hali baho yo‘q.")
            return

        if cmd == "/support_view":
            if not self.owner_only(u):
                self.send(uid, "Faqat bosh administrator ko‘rishi mumkin.")
                return
            rows = self.rows("SELECT * FROM admin_messages WHERE status='yangi' ORDER BY id DESC")
            if not rows:
                self.send(uid, "Yangi murojaatlar yo‘q.")
                return
            for r in rows:
                self.send(uid, f"#{r['id']} | user {r['user_id']}\n{r['text']}\n/qabul {r['id']}")
            return

        if cmd == "/qabul":
            if not self.owner_only(u):
                self.send(uid, "Faqat bosh administrator buni tasdiqlashi mumkin.")
                return
            msg_id = safe_int(text.split()[1], 0)
            self.write("UPDATE admin_messages SET status='qabul qilindi' WHERE id=?", (msg_id,))
            self.send(uid, "Murojaat qabul qilindi.")
            return

        if cmd == "/pro_activate":
            if not self.owner_only(u):
                self.send(uid, "Faqat admin PRO faollashtirishi mumkin.")
                return
            p = text.split()
            if len(p) < 3:
                self.send(uid, "Misol: /pro_activate 123456789 30")
                return
            user_id = safe_int(p[1], 0)
            days = safe_int(p[2], 30)
            expires = (datetime.now(TZ) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            self.write("UPDATE users SET pro_active=1, pro_expires_at=? WHERE id=?", (expires, user_id))
            self.send(uid, f"PRO obuna {days} kundan boshlab faollashtirildi.")
            self.notify(user_id, f"PRO obunangiz faollashtirildi. Tugash sanasi: {expires}")
            return

        if cmd == "/promo_set":
            if not self.owner_only(u):
                self.send(uid, "Faqat admin promo kodni o‘zgartira oladi.")
                return
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                self.send(uid, "Misol: /promo_set z1a2b3at")
                return
            os.environ["PROMO_CODE"] = parts[1].strip()
            self.send(uid, "Promo kod yangilandi.")
            return

        if cmd == "/promo_clear":
            if not self.owner_only(u):
                self.send(uid, "Faqat admin promo kodni o‘chirib qo‘ya oladi.")
                return
            os.environ.pop("PROMO_CODE", None)
            self.send(uid, "Promo kod o‘chirildi.")
            return

        if text == "Ma’lumotlarimni o‘chirish":
            self.write("INSERT OR IGNORE INTO data_delete_requests(user_id) VALUES(?)", (uid,))
            self.notify(self.owner, f"Ma’lumotlarni o‘chirish so‘rovi: {uid}")
            self.send(uid, "So‘rov yuborildi. Admin tasdiqlagandan keyin ma’lumotlaringiz o‘chiriladi.")
            return

        if cmd in {"/accept_delete", "/delete_confirm"}:
            if not self.owner_only(u):
                self.send(uid, "Faqat admin ma’lumotlarni o‘chirishni tasdiqlashi mumkin.")
                return
            user_id = safe_int(text.split()[1], 0)
            self.write("DELETE FROM users WHERE id=?", (user_id,))
            self.write("DELETE FROM sessions WHERE id=?", (user_id,))
            self.write("UPDATE data_delete_requests SET status='tasdiqlandi' WHERE user_id=?", (user_id,))
            self.send(uid, "Foydalanuvchi ma’lumotlari o‘chirildi.")
            self.notify(user_id, "Ma’lumotlaringiz admin tomonidan o‘chirildi.")
            return

        # Quiz response flow
        if self.get_session(uid) and self.get_session(uid)[0] == "quiz":
            if text.strip().upper() in {"A", "B", "C", "D"}:
                self.handle_quiz_step(uid, text.strip().upper())
                return

        self.send(uid, "Menyudan tanlang yoki /help bosing.")

    def process_promo(self, uid, code):
        if not code:
            self.send(uid, "Promo kodni kiriting.")
            return
        expected = os.environ.get("PROMO_CODE", "z1a2b3at").strip()
        if code != expected:
            self.send(uid, "Promo kod noto‘g‘ri.")
            return
        if self.one("SELECT 1 FROM promo_usage WHERE code=? AND user_id=?", (expected, uid)):
            self.send(uid, "Bu promo kodni avval ishlatgansiz.")
            return
        expires = (datetime.now(TZ) + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        self.write("UPDATE users SET pro_active=1, pro_expires_at=? WHERE id=?", (expires, uid))
        self.write("INSERT INTO promo_usage(code, user_id) VALUES(?, ?)", (expected, uid))
        self.send(uid, "1 oylik PRO obunangiz faollashtirildi.")

    def start_quiz(self, uid):
        from quiz_questions import QUESTIONS

        if self.user(uid) is None:
            self.send(uid, "Avval ro‘yxatdan o‘tishingiz kerak.")
            return
        state = {"index": 0, "answers": {}}
        self.session(uid, "quiz", state)
        self.ask_quiz_question(uid, state)

    def ask_quiz_question(self, uid, state):
        from quiz_questions import QUESTIONS

        idx = state["index"]
        q = QUESTIONS[idx]
        self.send(
            uid,
            f"Savol {idx + 1}/{len(QUESTIONS)}\n{q['question']}\nA) {q['options'][0]}\nB) {q['options'][1]}\nC) {q['options'][2]}\nD) {q['options'][3]}",
        )

    def handle_quiz_step(self, uid, answer):
        from quiz_questions import QUESTIONS

        session = self.get_session(uid)
        if session is None or session[0] != "quiz":
            return
        state = session[1]
        idx = state["index"]
        state["answers"][str(idx)] = answer
        idx += 1
        if idx >= len(QUESTIONS):
            score = 0
            for key, q in enumerate(QUESTIONS):
                if state["answers"].get(str(key)) == q["answer"]:
                    score += 1
            percent = int(round((score / len(QUESTIONS)) * 100))
            self.write(
                "INSERT INTO quiz_results(user_id, full_name, telegram_id, score, total, percent, best_score, best_percent, attempts) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    uid,
                    self.user(uid)["name"] if self.user(uid) else "",
                    uid,
                    score,
                    len(QUESTIONS),
                    percent,
                    score,
                    percent,
                    1,
                ),
            )
            self.clear_session(uid)
            self.send(uid, f"Test yakunlandi.\nTo‘g‘ri javoblar: {score}/{len(QUESTIONS)}\nFoiz: {percent}%\nNatija: {'Yuksak natija' if percent >= 70 else 'Yaxshi natija' if percent >= 50 else 'Qayta urinib ko‘ring'}")
            return
        state["index"] = idx
        self.session(uid, "quiz", state)
        self.ask_quiz_question(uid, state)


def create_web_app(state):
    async def ping(request):
        return web.json_response({"service": "Koordinatorlar platformasi", "status": "ok"})

    async def health(request):
        healthy = state.get("ready", False) and time.monotonic() - state.get("last_poll", 0) < 180
        return web.json_response({"status": "ok" if healthy else "starting_or_unavailable"}, status=200 if healthy else 503)

    app = web.Application()
    app.router.add_get("/", ping)
    app.router.add_get("/health", health)
    return app


def run_polling(token, owner, db_path, directions, stop, state):
    app = App(db_path, Telegram(token), owner, directions)
    try:
        me = app.api.call("getMe")
        hook = app.api.call("getWebhookInfo")
        if hook.get("url"):
            raise RuntimeError("Webhook yoqilgan. Polling uchun avval webhookni o‘chiring.")
        state["ready"] = True
        print("Bot ishga tushdi: @" + me["username"], flush=True)
        offset = int((app.one("SELECT value FROM meta WHERE key='offset'") or {"value": "0"})["value"])
        last_reminder = 0
        while not stop.is_set():
            try:
                updates = app.api.call("getUpdates", offset=offset, timeout=25, allowed_updates=["message"])
                state["last_poll"] = time.monotonic()
                for update in updates:
                    if stop.is_set():
                        break
                    if "message" in update:
                        msg = update["message"]
                        if msg.get("text") and msg["text"].strip().upper() in {"A", "B", "C", "D"}:
                            app.handle_quiz_step(msg["from"]["id"], msg["text"].strip().upper())
                        else:
                            app.handle(msg)
                    offset = update["update_id"] + 1
                    app.write("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", ("offset", str(offset)))
                if not stop.is_set() and time.monotonic() - last_reminder > 60:
                    last_reminder = time.monotonic()
            except RuntimeError as exc:
                print(str(exc), flush=True)
                stop.wait(5)
    finally:
        state["ready"] = False
        app.db.close()


async def serve(token, owner, port, db_path, directions):
    stop = threading.Event()
    shutdown = asyncio.Event()
    state = {"ready": False, "last_poll": 0.0}
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown.set)
        except (NotImplementedError, RuntimeError):
            pass
    runner = web.AppRunner(create_web_app(state), access_log=None)
    await runner.setup()
    worker = waiter = None
    try:
        await web.TCPSite(runner, host="0.0.0.0", port=port).start()
        print(f"HTTP server: 0.0.0.0:{port}", flush=True)
        worker = asyncio.create_task(asyncio.to_thread(run_polling, token, owner, db_path, directions, stop, state))
        waiter = asyncio.create_task(shutdown.wait())
        done, _ = await asyncio.wait({worker, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if worker in done:
            await worker
    finally:
        stop.set()
        if waiter:
            waiter.cancel()
        await runner.cleanup()
        if worker:
            await asyncio.shield(worker)


def main():
    load_env()
    token = os.environ.get("BOT_TOKEN", "").strip()
    owner = os.environ.get("OWNER_ID", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN environment variable bo‘sh bo‘lishi mumkin emas.")
    if not owner.isdigit() or int(owner) <= 0:
        raise SystemExit("OWNER_ID musbat raqam bo‘lishi kerak.")
    try:
        port = int(os.environ.get("PORT", "10000"))
        if not 1 <= port <= 65535:
            raise ValueError()
    except ValueError:
        raise SystemExit("PORT 1–65535 oralig‘idagi son bo‘lishi kerak.") from None
    db_path = Path(os.environ.get("DATABASE_PATH", str(ROOT / "data.sqlite3")))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    try:
        asyncio.run(serve(token, int(owner), int(port), str(db_path), config["directions"]))
    except KeyboardInterrupt:
        print("Bot to‘xtatildi.", flush=True)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()

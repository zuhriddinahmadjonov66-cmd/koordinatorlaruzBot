"""KoordinatorlarUzBot uchun faqat bot egasiga ochiq Telegram admin panel.

Integratsiya:
    from admin_panel import build_admin_router
    dp.include_router(build_admin_router(DB_PATH, OWNER_ID))

Ushbu panel mavjud SQLite jadvallaridagi ma'lumotlarni o'qiydi.
"""

import html
import sqlite3
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


def build_admin_router(db_path: str, owner_id: int) -> Router:
    router = Router(name="owner_admin")

    def allowed(user_id: int, chat_type: str) -> bool:
        return owner_id > 0 and user_id == owner_id and chat_type == "private"

    def menu() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Umumiy statistika", callback_data="owner_stats")],
            [InlineKeyboardButton(text="👥 Oxirgi 10 foydalanuvchi", callback_data="owner_recent")],
            [InlineKeyboardButton(text="🧭 Yo‘nalishlar bo‘yicha", callback_data="owner_directions")],
            [InlineKeyboardButton(text="📋 Topshiriqlar va hisobotlar", callback_data="owner_tasks")],
        ])

    def back() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Admin menyusi", callback_data="owner_home")]
        ])

    def get_stats():
        with sqlite3.connect(db_path) as conn:
            recent_cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            return {
                "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "new7": conn.execute(
                    "SELECT COUNT(*) FROM users WHERE registered_at >= ?", (recent_cutoff,)
                ).fetchone()[0],
                "tasks": conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                "reports": conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0],
                "dls": conn.execute("SELECT COUNT(DISTINCT user_id) FROM dls_registrations").fetchone()[0],
                "quizzes": conn.execute("SELECT COUNT(*) FROM quiz_results").fetchone()[0],
            }

    @router.message(Command("admin"))
    @router.message(F.text == "🔐 Admin panel")
    async def admin_home_message(message: Message):
        if not allowed(message.from_user.id, message.chat.type):
            await message.answer("⛔ Bu bo‘lim faqat bot egasi uchun.")
            return
        await message.answer("🔐 <b>Bosh admin paneli</b>\nBo‘limni tanlang:",
                             reply_markup=menu(), parse_mode="HTML")

    @router.callback_query(F.data == "owner_home")
    async def admin_home_callback(call: CallbackQuery):
        if not allowed(call.from_user.id, call.message.chat.type if call.message else ""):
            await call.answer("Ruxsat yo‘q", show_alert=True)
            return
        await call.answer()
        await call.message.edit_text("🔐 <b>Bosh admin paneli</b>\nBo‘limni tanlang:",
                                     reply_markup=menu(), parse_mode="HTML")

    @router.callback_query(F.data == "owner_stats")
    async def admin_stats(call: CallbackQuery):
        if not allowed(call.from_user.id, call.message.chat.type if call.message else ""):
            await call.answer("Ruxsat yo‘q", show_alert=True)
            return
        await call.answer()
        s = get_stats()
        text = (
            "📊 <b>Bot statistikasi</b>\n\n"
            f"👥 Ro‘yxatdan o‘tganlar: <b>{s['users']}</b>\n"
            f"🆕 Oxirgi 7 kunda ro‘yxatdan o‘tganlar: <b>{s['new7']}</b>\n"
            f"📋 Topshiriqlar: <b>{s['tasks']}</b>\n"
            f"📥 Hisobotlar: <b>{s['reports']}</b>\n"
            f"🏆 DLS ishtirokchilari: <b>{s['dls']}</b>\n"
            f"🧠 Yakunlangan testlar: <b>{s['quizzes']}</b>\n\n"
            "ℹ️ Sonlar hozirgi ma’lumotlar bazasidan olinadi."
        )
        await call.message.edit_text(text, reply_markup=back(), parse_mode="HTML")

    @router.callback_query(F.data == "owner_recent")
    async def admin_recent(call: CallbackQuery):
        if not allowed(call.from_user.id, call.message.chat.type if call.message else ""):
            await call.answer("Ruxsat yo‘q", show_alert=True)
            return
        await call.answer()
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT full_name, registered_at FROM users "
                "ORDER BY registered_at DESC, user_id DESC LIMIT 10"
            ).fetchall()
        lines = ["👥 <b>Oxirgi 10 foydalanuvchi</b>", ""]
        if not rows:
            lines.append("Hali ro‘yxatdan o‘tgan foydalanuvchi yo‘q.")
        for i, (name, date) in enumerate(rows, 1):
            lines.append(f"{i}. {html.escape(name or 'Ismsiz')} — {html.escape(date or 'Sana yo‘q')}")
        await call.message.edit_text("\n".join(lines), reply_markup=back(), parse_mode="HTML")

    @router.callback_query(F.data == "owner_directions")
    async def admin_directions(call: CallbackQuery):
        if not allowed(call.from_user.id, call.message.chat.type if call.message else ""):
            await call.answer("Ruxsat yo‘q", show_alert=True)
            return
        await call.answer()
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT direction, COUNT(*) FROM users GROUP BY direction ORDER BY COUNT(*) DESC"
            ).fetchall()
        lines = ["🧭 <b>Yo‘nalishlar bo‘yicha a’zolar</b>", ""]
        if not rows:
            lines.append("Hali ma’lumot yo‘q.")
        for name, count in rows:
            lines.append(f"• {html.escape(name or 'Tanlanmagan')}: <b>{count}</b>")
        await call.message.edit_text("\n".join(lines), reply_markup=back(), parse_mode="HTML")

    @router.callback_query(F.data == "owner_tasks")
    async def admin_tasks(call: CallbackQuery):
        if not allowed(call.from_user.id, call.message.chat.type if call.message else ""):
            await call.answer("Ruxsat yo‘q", show_alert=True)
            return
        await call.answer()
        with sqlite3.connect(db_path) as conn:
            task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            report_count = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
            pending_count = conn.execute(
                "SELECT COUNT(*) FROM reports WHERE status = ?", ("Baholash kutilmoqda",)
            ).fetchone()[0]
        text = (
            "📋 <b>Topshiriqlar va hisobotlar</b>\n\n"
            f"Yaratilgan topshiriqlar: <b>{task_count}</b>\n"
            f"Jami hisobotlar: <b>{report_count}</b>\n"
            f"Baholashni kutayotganlar: <b>{pending_count}</b>\n\n"
            "Topshiriq yaratish uchun oldingi 🧭 Koordinator panelidan foydalaning."
        )
        await call.message.edit_text(text, reply_markup=back(), parse_mode="HTML")

    return router

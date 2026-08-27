#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot: Mohit Tazkiah Library - Complete Arabic Version
Python 3.10/3.11/3.12/3.13 Compatible
Parse Mode: HTML
"""

import json
import os
import logging
from datetime import datetime
from urllib.parse import urlparse

# تحميل متغيرات البيئة من ملف .env محلياً إن وجد
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0").strip() or "0")
DATA_DIR = os.path.join(os.path.dirname(__file__), "bot-data")

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# التأكد من وجود مجلد البيانات وملف الفهرس الأولي
os.makedirs(DATA_DIR, exist_ok=True)
index_file_path = os.path.join(DATA_DIR, "index.json")
if not os.path.exists(index_file_path):
    with open(index_file_path, "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False, indent=2)

def load_json(filename):
    file_path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(file_path):
        return {} if filename == "index.json" else []
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception as e:
            logger.error(f"Error reading {filename}: {e}")
            return {} if filename == "index.json" else []

INDEX = load_json("index.json")
SERIES_DATA = {sid: load_json(f"{sid}.json") for sid in INDEX}


def sync_database():
    """Mirror Telegram-admin edits into the durable MySQL database when configured."""
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        return
    try:
        import pymysql
        parsed = urlparse(database_url)
        connection = pymysql.connect(
            host=parsed.hostname,
            port=parsed.port or 3306,
            user=parsed.username,
            password=parsed.password,
            database=(parsed.path or "/").lstrip("/"),
            charset="utf8mb4",
            autocommit=False,
            connect_timeout=10,
        )
        try:
            with connection.cursor() as cursor:
                if INDEX:
                    placeholders = ",".join(["%s"] * len(INDEX))
                    cursor.execute(f"DELETE FROM library_series WHERE id NOT IN ({placeholders})", tuple(INDEX.keys()))
                else:
                    cursor.execute("DELETE FROM library_series")
                for order, (sid, info) in enumerate(INDEX.items(), 1):
                    cursor.execute(
                        "INSERT INTO library_series (id,title,topic,description,sortOrder) VALUES (%s,%s,%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE title=VALUES(title),topic=VALUES(topic),description=VALUES(description),sortOrder=VALUES(sortOrder)",
                        (sid, info.get("title", ""), info.get("topic", ""), info.get("description"), order),
                    )
                for sid, messages in SERIES_DATA.items():
                    cursor.execute("DELETE FROM library_messages WHERE seriesId=%s", (sid,))
                    for order, msg in enumerate(messages, 1):
                        cursor.execute(
                            "INSERT INTO library_messages (seriesId,sourceId,title,topic,content,sortOrder) VALUES (%s,%s,%s,%s,%s,%s)",
                            (sid, msg.get("id", order), msg.get("title", ""), msg.get("topic", ""), msg.get("text", ""), order),
                        )
            connection.commit()
        finally:
            connection.close()
    except Exception as error:
        logger.warning("Durable library sync skipped: %s", error)

# ─── HTML Escape ───
def esc(t):
    if not t:
        return ""
    return (str(t)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

# ─── User Data ───
def get_ud(ctx):
    if "ud" not in ctx.chat_data:
        ctx.chat_data["ud"] = {
            "bookmarks": {},
            "favorites": [],
            "progress": {},
            "settings": {"theme": "dark", "font": "medium", "notif": True},
            "stats": {"read": 0, "started": set(), "searches": 0, "last": None}
        }
    return ctx.chat_data["ud"]

# ─── Main Menu Keyboard ───
def main_kb():
    k = []
    row = []
    for i, (sid, info) in enumerate(INDEX.items(), 1):
        icon = info.get('icon', '📚')
        title = info.get('title', sid)
        display_title = title.split(' ', 1)[1] if ' ' in title else title
        row.append(InlineKeyboardButton(
            f"{icon} {display_title}",
            callback_data=f"s:{sid}"
        ))
        if i % 2 == 0:
            k.append(row)
            row = []
    if row:
        k.append(row)
    k.append([
        InlineKeyboardButton("🔖 الإشارات المرجعية", callback_data="m:bm"),
        InlineKeyboardButton("⭐ المفضلة", callback_data="m:fav")
    ])
    k.append([
        InlineKeyboardButton("📊 إحصائياتي", callback_data="m:st"),
        InlineKeyboardButton("🔍 البحث", callback_data="m:sr")
    ])
    k.append([
        InlineKeyboardButton("⚙️ الإعدادات", callback_data="m:set"),
        InlineKeyboardButton("ℹ️ عن المكتبة", callback_data="m:ab")
    ])
    return InlineKeyboardMarkup(k)

# ─── Reading Navigation Keyboard ───
def read_kb(sid, mid, total, bm=False, fav=False):
    nav = []
    if mid > 1:
        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"r:{sid}:{mid-1}"))
    nav.append(InlineKeyboardButton(f"{mid} / {total}", callback_data="noop"))
    if mid < total:
        nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"r:{sid}:{mid+1}"))
    act = [
        InlineKeyboardButton("🔖 إزالة الإشارة" if bm else "🔖 إشارة مرجعية", callback_data=f"a:bm:{sid}:{mid}"),
        InlineKeyboardButton("⭐ إزالة المفضلة" if fav else "⭐ أضف للمفضلة", callback_data=f"a:fav:{sid}:{mid}")
    ]
    ext = [
        InlineKeyboardButton("🔢 انتقال سريع", callback_data=f"a:jp:{sid}"),
        InlineKeyboardButton("🔍 بحث في السلسلة", callback_data=f"a:sr:{sid}"),
        InlineKeyboardButton("❓ استفسار للمؤلف", callback_data=f"a:in:{sid}:{mid}")
    ]
    back = [
        InlineKeyboardButton("📋 قائمة السلسلة", callback_data=f"s:{sid}"),
        InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="m:main")
    ]
    return InlineKeyboardMarkup([nav, act, ext, back])

# ─── Format Message Header ───
def fmt_msg(msg, title, total):
    mid = msg.get('id', 1)
    date_str = msg.get('date', 'غير محدد')
    length_str = msg.get('length', len(msg.get('text', '')))
    return (
        f"<b>{esc(title)}</b>\n"
        f"<code>━━━━━━━━━━━━━━━━━━━━</code>\n"
        f"📄 <b>الرسالة {mid}</b> من أصل <b>{total}</b>\n"
        f"📅 {esc(date_str)} | 📝 {length_str} حرف\n"
        f"<code>━━━━━━━━━━━━━━━━━━━━</code>\n\n"
        f"{esc(msg.get('text', ''))}"
    )

# ─── /start ───
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ud = get_ud(ctx)
    ud["stats"]["last"] = datetime.now().isoformat()
    total_all = sum(len(SERIES_DATA.get(sid, [])) for sid in SERIES_DATA)
    txt = (
        f"أهلاً بك <b>{esc(u.first_name)}</b> في 🤖 <b>مكتبة المحيط للتزكية</b>\n\n"
        f"📚 د. سامي المؤيد — {len(INDEX)} سلسلة — {total_all} رسالة\n"
        f"📅 2017 – 2026\n\n"
        f"✨ <b>المميزات:</b>\n"
        f"• قراءة منظمة لكل سلسلة\n"
        f"• 🔍 بحث ذكي في كل المكتبة\n"
        f"• 🔖 إشارات مرجعية + حفظ التقدم\n"
        f"• ⭐ مفضلة + ❓ استفسار للمؤلف\n\n"
        f"👇 اختر سلسلة للبدء:"
    )
    await update.message.reply_text(txt, parse_mode="HTML", reply_markup=main_kb())

# ─── /help ───
async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>دليل الاستخدام</b>\n\n"
        "/start — القائمة الرئيسية\n"
        "/help — هذا الدليل\n"
        "/continue — مواصلة القراءة من آخر نقطة\n"
        "/stats — إحصائيات قراءتك\n"
        "/whoami — معرفة معرّف حسابك في تيليجرام\n"
        "/admin — لوحة إدارة محتوى المكتبة (للمسؤول فقط)\n\n"
        "💡 يمكنك أيضاً إرسال أي كلمة للبحث الفوري في جميع محتويات المكتبة.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 رئيسية", callback_data="m:main")]])
    )

# ─── /continue ───
async def cont_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ud = get_ud(ctx)
    prog = ud.get("progress", {})
    if not prog:
        await update.message.reply_text(
            "لم تبدأ قراءة أي سلسلة بعد. اضغط /start للبدء.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 رئيسية", callback_data="m:main")]])
        )
        return
    k = []
    for sid, mid in prog.items():
        if sid in INDEX:
            info = INDEX[sid]
            k.append([InlineKeyboardButton(
                f"{info.get('icon', '📚')} {info.get('title', sid)} (رسالة {mid})",
                callback_data=f"r:{sid}:{mid}"
            )])
    k.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="m:main")])
    await update.message.reply_text("📌 <b>مواصلة القراءة:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(k))

# ─── /stats ───
async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ud = get_ud(ctx)
    st = ud["stats"]
    txt = (
        f"📊 <b>إحصائيات قراءتك</b>\n\n"
        f"📖 رسائل مقروءة: <b>{st['read']}</b>\n"
        f"📚 سلاسل بدأتها: <b>{len(st['started'])}</b>\n"
        f"🔍 عمليات بحث: <b>{st['searches']}</b>\n"
        f"🔖 إشارات مرجعية: <b>{sum(len(v) for v in ud['bookmarks'].values())}</b>\n"
        f"⭐ مفضلة: <b>{len(ud['favorites'])}</b>"
    )
    await update.message.reply_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="m:main")]]
    ))

# ─── /whoami ───
async def whoami_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        await update.message.reply_text("تعذر قراءة معرّف حساب Telegram.")
        return
    await update.message.reply_text(f"🆔 معرّف حسابك (Telegram ID) هو:\n<code>{user.id}</code>", parse_mode="HTML")

# ─── إدارة المكتبة داخل Telegram ───
def save_library():
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(INDEX, f, ensure_ascii=False, indent=2)
    for sid, messages in SERIES_DATA.items():
        with open(os.path.join(DATA_DIR, f"{sid}.json"), "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
    sync_database()

def admin_user(update):
    return update.effective_user and ADMIN_ID != 0 and update.effective_user.id == ADMIN_ID

def admin_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 إدارة السلاسل", callback_data="ad:series")],
        [InlineKeyboardButton("➕ إنشاء سلسلة", callback_data="ad:newseries")],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="m:main")],
    ])

async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or ADMIN_ID == 0 or user.id != ADMIN_ID:
        logger.warning("/admin denied: telegram_user_id=%s configured_admin_id=%s", user.id if user else None, ADMIN_ID)
        await update.message.reply_text("⛔ هذا الأمر مخصص لمدير المكتبة فقط.")
        return
    await update.message.reply_text(
        "🔐 <b>إدارة مكتبة المحيط</b>\n\nاختر العملية المطلوبة. التغييرات تُحفظ في ملفات المكتبة فوراً.",
        parse_mode="HTML", reply_markup=admin_menu_kb()
    )

async def admin_callback(update, ctx, data):
    q = update.callback_query
    if not admin_user(update):
        await q.answer("غير مصرح لك", show_alert=True)
        return True
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else "home"
    if action == "home":
        await q.edit_message_text("🔐 <b>إدارة مكتبة المحيط</b>\n\nاختر العملية المطلوبة:", parse_mode="HTML", reply_markup=admin_menu_kb())
    elif action == "series":
        rows = [[InlineKeyboardButton(f"{i}. {info['title']}", callback_data=f"ad:sel:{sid}")] for i, (sid, info) in enumerate(INDEX.items(), 1)]
        rows += [[InlineKeyboardButton("➕ إنشاء سلسلة", callback_data="ad:newseries")], [InlineKeyboardButton("⬅️ رجوع", callback_data="ad:home")]]
        await q.edit_message_text("📚 <b>اختر السلسلة لإدارتها:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
    elif action == "newseries":
        ctx.chat_data["admin_state"] = {"kind": "new_series"}
        await q.edit_message_text("➕ أرسل بيانات السلسلة بهذا الشكل:\n\n<b>المعرّف | العنوان | الموضوع</b>\n\nمثال: سلسلة_جديدة | عنوان السلسلة | موضوعها", parse_mode="HTML")
    elif action == "sel":
        sid = parts[2]
        info = INDEX.get(sid, {})
        msgs_count = len(SERIES_DATA.get(sid, []))
        await q.edit_message_text(
            f"📚 <b>{esc(info.get('title', sid))}</b>\n\nالموضوع: {esc(info.get('topic', info.get('description', 'غير محدد')))}\nالرسائل: {msgs_count}",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ تعديل العنوان", callback_data=f"ad:editseries:{sid}:title"), InlineKeyboardButton("✏️ تعديل الموضوع", callback_data=f"ad:editseries:{sid}:topic")],
                [InlineKeyboardButton("📝 إدارة الرسائل", callback_data=f"ad:msgs:{sid}")],
                [InlineKeyboardButton("⬆️ رفع السلسلة", callback_data=f"ad:move:{sid}:up"), InlineKeyboardButton("⬇️ خفض السلسلة", callback_data=f"ad:move:{sid}:down")],
                [InlineKeyboardButton("🗑 حذف السلسلة", callback_data=f"ad:delseriesconfirm:{sid}")],
                [InlineKeyboardButton("⬅️ السلاسل", callback_data="ad:series")]
            ])
        )
    elif action == "editseries":
        sid, field = parts[2], parts[3]
        ctx.chat_data["admin_state"] = {"kind": "edit_series", "sid": sid, "field": field}
        label = "العنوان" if field == "title" else "الموضوع"
        await q.edit_message_text(f"✏️ أرسل {label} الجديد للسلسلة:\n<b>{esc(INDEX[sid].get('title', sid))}</b>", parse_mode="HTML")
    elif action == "msgs":
        sid = parts[2]
        rows = []
        for msg in SERIES_DATA.get(sid, []):
            rows.append([InlineKeyboardButton(f"{msg['id']}. {msg.get('title', msg['text'][:35])}", callback_data=f"ad:msg:{sid}:{msg['id']}")])
        rows += [[InlineKeyboardButton("➕ إضافة رسالة", callback_data=f"ad:newmsg:{sid}")], [InlineKeyboardButton("⬅️ السلسلة", callback_data=f"ad:sel:{sid}")]]
        await q.edit_message_text("📝 <b>اختر الرسالة:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
    elif action == "msg":
        sid, mid = parts[2], int(parts[3])
        msg = next((m for m in SERIES_DATA.get(sid, []) if m['id'] == mid), None)
        if not msg:
            await q.answer("الرسالة غير موجودة")
            return True
        await q.edit_message_text(
            f"📝 <b>الرسالة {mid}</b>\n\nالعنوان: {esc(msg.get('title', 'بدون عنوان'))}\nالموضوع: {esc(msg.get('topic', 'غير محدد'))}\n\n{esc(msg.get('text', '')[:500])}", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ العنوان", callback_data=f"ad:editmsg:{sid}:{mid}:title"), InlineKeyboardButton("✏️ الموضوع", callback_data=f"ad:editmsg:{sid}:{mid}:topic")],
                [InlineKeyboardButton("✏️ المحتوى", callback_data=f"ad:editmsg:{sid}:{mid}:text")],
                [InlineKeyboardButton("⬆️ رفع", callback_data=f"ad:move_msg:{sid}:{mid}:up"), InlineKeyboardButton("⬇️ خفض", callback_data=f"ad:move_msg:{sid}:{mid}:down")],
                [InlineKeyboardButton("🗑 حذف الرسالة", callback_data=f"ad:delmsgconfirm:{sid}:{mid}")],
                [InlineKeyboardButton("⬅️ الرسائل", callback_data=f"ad:msgs:{sid}")]
            ])
        )
    elif action == "editmsg":
        sid, mid, field = parts[2], int(parts[3]), parts[4]
        ctx.chat_data["admin_state"] = {"kind": "edit_msg", "sid": sid, "mid": mid, "field": field}
        labels = {"title": "العنوان", "topic": "الموضوع", "text": "المحتوى"}
        await q.edit_message_text(f"✏️ أرسل {labels.get(field, field)} الجديد للرسالة {mid}:")
    elif action == "newmsg":
        sid = parts[2]
        ctx.chat_data["admin_state"] = {"kind": "new_msg", "sid": sid}
        await q.edit_message_text("➕ أرسل الرسالة بهذا الشكل:\n\n<b>العنوان | الموضوع | المحتوى</b>", parse_mode="HTML")
    elif action == "delseriesconfirm":
        sid = parts[2]
        await q.edit_message_text(f"⚠️ هل تريد حذف السلسلة <b>{esc(INDEX.get(sid, {}).get('title', sid))}</b> وجميع رسائلها؟", parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("نعم، احذف", callback_data=f"ad:delseries:{sid}"), InlineKeyboardButton("إلغاء", callback_data=f"ad:sel:{sid}")]]))
    elif action == "delseries":
        sid = parts[2]
        INDEX.pop(sid, None)
        SERIES_DATA.pop(sid, None)
        save_library()
        await q.edit_message_text("✅ حُذفت السلسلة ورسائلها.", reply_markup=admin_menu_kb())
    elif action == "delmsgconfirm":
        sid, mid = parts[2], int(parts[3])
        await q.edit_message_text("⚠️ هل تريد حذف هذه الرسالة؟", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("نعم، احذف", callback_data=f"ad:delmsg:{sid}:{mid}"), InlineKeyboardButton("إلغاء", callback_data=f"ad:msg:{sid}:{mid}")]]))
    elif action == "delmsg":
        sid, mid = parts[2], int(parts[3])
        SERIES_DATA[sid] = [m for m in SERIES_DATA.get(sid, []) if m['id'] != mid]
        for i, m in enumerate(SERIES_DATA[sid], 1):
            m['id'] = i
        save_library()
        await q.edit_message_text("✅ حُذفت الرسالة وأعيد ترقيم الرسائل.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ الرسائل", callback_data=f"ad:msgs:{sid}")]]))
    elif action in {"move", "move_msg"}:
        sid = parts[2]
        if action == "move":
            direction = parts[3]
            items = list(INDEX.items())
            pos = [x[0] for x in items].index(sid)
            other = pos - 1 if direction == "up" else pos + 1
            if 0 <= other < len(items):
                items[pos], items[other] = items[other], items[pos]
                INDEX.clear()
                INDEX.update(items)
            save_library()
            await q.answer("✅ تم تحديث الترتيب")
            await admin_callback(update, ctx, f"ad:sel:{sid}")
        else:
            mid = int(parts[3])
            direction = parts[4]
            msgs = SERIES_DATA[sid]
            pos = next(i for i, m in enumerate(msgs) if m['id'] == mid)
            other = pos - 1 if direction == "up" else pos + 1
            if 0 <= other < len(msgs):
                msgs[pos], msgs[other] = msgs[other], msgs[pos]
                for i, m in enumerate(msgs, 1):
                    m['id'] = i
            save_library()
            await q.answer("✅ تم تحديث الترتيب")
            await admin_callback(update, ctx, f"ad:msg:{sid}:{mid}")
    else:
        await q.answer("عملية غير معروفة")
    return True

# ─── Callback Handler ───
async def cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    ud = get_ud(ctx)

    if d.startswith("ad:"):
        await admin_callback(update, ctx, d)
        return

    if d == "noop":
        await q.answer("📍")
        return

    if d == "m:main":
        await q.edit_message_text("👇 <b>القائمة الرئيسية</b> — اختر سلسلة:", parse_mode="HTML", reply_markup=main_kb())
        return

    if d == "m:ab":
        total_all = sum(len(SERIES_DATA.get(sid, [])) for sid in SERIES_DATA)
        await q.edit_message_text(
            f"ℹ️ <b>عن مكتبة المحيط للتزكية</b>\n\n"
            f"📚 قناة المحيط لتزكية النفس\n"
            f"✍️ د. سامي المؤيد\n"
            f"📅 الفترة: 2017 – 2026\n"
            f"📝 إجمالي الرسائل: {total_all} رسالة\n"
            f"📂 عدد السلاسل: {len(INDEX)}\n"
            f"🎯 تربية روحية — فهم سليم — عقلانية إسلامية",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 رجوع", callback_data="m:main")]])
        )
        return

    if d == "m:set":
        s = ud["settings"]
        await q.edit_message_text(
            f"⚙️ <b>الإعدادات</b>\n\n"
            f"🌙 الوضع: <b>{s['theme']}</b>\n"
            f"🔤 حجم الخط: <b>{s['font']}</b>\n"
            f"🔔 الإشعارات: <b>{'مفعلة' if s['notif'] else 'معطلة'}</b>\n\n"
            f"اختر ما تريد تغييره:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🌙/☀️ تبديل الوضع", callback_data="set:theme")],
                [InlineKeyboardButton("🔔 تبديل الإشعارات", callback_data="set:notif")],
                [InlineKeyboardButton("🏠 رجوع", callback_data="m:main")]
            ])
        )
        return

    if d.startswith("set:"):
        k = d.split(":")[1]
        if k == "theme":
            ud["settings"]["theme"] = "light" if ud["settings"]["theme"] == "dark" else "dark"
        elif k == "notif":
            ud["settings"]["notif"] = not ud["settings"]["notif"]
        await cb(update, ctx)
        return

    if d == "m:bm":
        bm = ud.get("bookmarks", {})
        if not any(bm.values()):
            await q.edit_message_text(
                "🔖 <b>الإشارات المرجعية</b>\n\nلا توجد إشارات بعد.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 رجوع", callback_data="m:main")]])
            )
            return
        k = []
        for sid, mids in bm.items():
            for mid in mids:
                if sid in INDEX:
                    info = INDEX[sid]
                    k.append([InlineKeyboardButton(
                        f"{info.get('icon', '📚')} {info.get('title', sid)} — رسالة {mid}",
                        callback_data=f"r:{sid}:{mid}"
                    )])
        k.append([InlineKeyboardButton("🏠 رجوع", callback_data="m:main")])
        await q.edit_message_text("🔖 <b>إشاراتك المرجعية:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(k))
        return

    if d == "m:fav":
        fav = ud.get("favorites", [])
        if not fav:
            await q.edit_message_text(
                "⭐ <b>المفضلة</b>\n\nلا توجد رسائل مفضلة بعد.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 رجوع", callback_data="m:main")]])
            )
            return
        k = []
        for sid, mid in fav:
            if sid in INDEX:
                info = INDEX[sid]
                k.append([InlineKeyboardButton(
                    f"{info.get('icon', '📚')} {info.get('title', sid)} — رسالة {mid}",
                    callback_data=f"r:{sid}:{mid}"
                )])
        k.append([InlineKeyboardButton("🏠 رجوع", callback_data="m:main")])
        await q.edit_message_text("⭐ <b>رسائلك المفضلة:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(k))
        return

    if d == "m:st":
        st = ud["stats"]
        await q.edit_message_text(
            f"📊 <b>إحصائياتك</b>\n\n"
            f"📖 مقروءة: <b>{st['read']}</b>\n"
            f"📚 سلاسل: <b>{len(st['started'])}</b>\n"
            f"🔍 بحث: <b>{st['searches']}</b>\n"
            f"🔖 إشارات: <b>{sum(len(v) for v in ud['bookmarks'].values())}</b>\n"
            f"⭐ مفضلة: <b>{len(ud['favorites'])}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 رجوع", callback_data="m:main")]])
        )
        return

    if d == "m:sr":
        await q.edit_message_text("🔍 <b>البحث في المكتبة</b>\nأرسل الكلمة أو العبارة التي تبحث عنها:", parse_mode="HTML")
        ctx.chat_data["mode"] = "search_global"
        return

    if d.startswith("s:"):
        sid = d.split(":", 1)[1]
        if sid not in INDEX:
            await q.answer("السلسلة غير موجودة")
            return
        info = INDEX[sid]
        msgs = SERIES_DATA.get(sid, [])
        total = len(msgs)
        last = ud.get("progress", {}).get(sid, 0)
        keywords_str = ', '.join(info.get('keywords', []))
        txt = (
            f"{info.get('icon', '📚')} <b>{esc(info.get('title', sid))}</b>\n\n"
            f"{esc(info.get('description', ''))}\n\n"
            f"📨 إجمالي الرسائل: <b>{total}</b>\n"
            f"📅 الفترة: {esc(info.get('period', ''))}\n"
            f"🏷️ الكلمات المفتاحية: {esc(keywords_str)}\n"
        )
        if last:
            txt += f"\n📌 آخر قراءة: رسالة <b>{last}</b>"
        k = []
        if total > 0:
            k.append([InlineKeyboardButton("▶️ ابدأ من الأولى", callback_data=f"r:{sid}:1")])
            if last:
                k[0].append(InlineKeyboardButton("📌 أكمل من آخر نقطة", callback_data=f"r:{sid}:{last}"))
            k.append([InlineKeyboardButton("🔍 بحث في السلسلة", callback_data=f"a:sr:{sid}")])
        k.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="m:main")])
        await q.edit_message_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(k))
        return

    if d.startswith("r:"):
        _, sid, mid = d.split(":")
        mid = int(mid)
        msgs = SERIES_DATA.get(sid, [])
        info = INDEX.get(sid, {})
        total = len(msgs)
        if total == 0:
            await q.answer("لا توجد رسائل في هذه السلسلة بعد")
            return
        if mid < 1:
            mid = 1
        if mid > total:
            mid = total
        ud["progress"][sid] = mid
        ud["stats"]["read"] += 1
        ud["stats"]["started"].add(sid)
        msg = msgs[mid - 1]
        is_bm = mid in ud.get("bookmarks", {}).get(sid, [])
        is_fav = (sid, mid) in ud.get("favorites", [])
        await q.edit_message_text(fmt_msg(msg, info.get("title", sid), total), parse_mode="HTML", reply_markup=read_kb(sid, mid, total, is_bm, is_fav))
        return

    if d.startswith("a:bm:"):
        _, _, sid, mid = d.split(":")
        mid = int(mid)
        if sid not in ud["bookmarks"]:
            ud["bookmarks"][sid] = []
        if mid in ud["bookmarks"][sid]:
            ud["bookmarks"][sid].remove(mid)
            await q.answer("🔖 تمت إزالة الإشارة المرجعية")
        else:
            ud["bookmarks"][sid].append(mid)
            await q.answer("🔖 تمت إضافة الإشارة المرجعية")
        msgs = SERIES_DATA.get(sid, [])
        info = INDEX.get(sid, {})
        total = len(msgs)
        msg = msgs[mid - 1]
        is_bm = mid in ud.get("bookmarks", {}).get(sid, [])
        is_fav = (sid, mid) in ud.get("favorites", [])
        await q.edit_message_text(fmt_msg(msg, info.get("title", sid), total), parse_mode="HTML", reply_markup=read_kb(sid, mid, total, is_bm, is_fav))
        return

    if d.startswith("a:fav:"):
        _, _, sid, mid = d.split(":")
        mid = int(mid)
        key = (sid, mid)
        if key in ud["favorites"]:
            ud["favorites"].remove(key)
            await q.answer("⭐ تمت إزالة المفضلة")
        else:
            ud["favorites"].append(key)
            await q.answer("⭐ تمت إضافة المفضلة")
        msgs = SERIES_DATA.get(sid, [])
        info = INDEX.get(sid, {})
        total = len(msgs)
        msg = msgs[mid - 1]
        is_bm = mid in ud.get("bookmarks", {}).get(sid, [])
        is_fav = (sid, mid) in ud.get("favorites", [])
        await q.edit_message_text(fmt_msg(msg, info.get("title", sid), total), parse_mode="HTML", reply_markup=read_kb(sid, mid, total, is_bm, is_fav))
        return

    if d.startswith("a:jp:"):
        sid = d.split(":", 2)[2]
        total = len(SERIES_DATA.get(sid, []))
        await q.edit_message_text(
            f"🔢 <b>الانتقال السريع</b>\n"
            f"أرسل رقم الرسالة (1 – {total}):",
            parse_mode="HTML"
        )
        ctx.chat_data["mode"] = "jump"
        ctx.chat_data["jsid"] = sid
        ctx.chat_data["jtotal"] = total
        return

    if d.startswith("a:sr:"):
        sid = d.split(":", 2)[2]
        await q.edit_message_text("🔍 <b>البحث في السلسلة</b>\nأرسل الكلمة أو العبارة:", parse_mode="HTML")
        ctx.chat_data["mode"] = "search_series"
        ctx.chat_data["ssid"] = sid
        return

    if d.startswith("a:in:"):
        _, _, sid, mid = d.split(":")
        mid = int(mid)
        info = INDEX.get(sid, {})
        await q.edit_message_text(
            f"❓ <b>إرسال استفسار للمؤلف</b>\n\n"
            f"حول: {esc(info.get('title', sid))} — رسالة {mid}\n\n"
            f"اكتب استفسارك الآن وسيتم إرساله للمشرف:",
            parse_mode="HTML"
        )
        ctx.chat_data["mode"] = "inquiry"
        ctx.chat_data["isid"] = sid
        ctx.chat_data["imid"] = mid
        return

# ─── Text Handler ───
async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    ud = get_ud(ctx)
    admin_state = ctx.chat_data.get("admin_state")
    if admin_state and update.effective_user and ADMIN_ID != 0 and update.effective_user.id == ADMIN_ID:
        kind = admin_state.get("kind")
        try:
            if kind == "edit_series":
                INDEX[admin_state["sid"]][admin_state["field"]] = txt
                save_library()
                reply = "✅ تم تحديث بيانات السلسلة."
            elif kind == "edit_msg":
                msg = next(m for m in SERIES_DATA[admin_state["sid"]] if m["id"] == admin_state["mid"])
                msg[admin_state["field"]] = txt
                if admin_state["field"] == "text":
                    msg["length"] = len(txt)
                save_library()
                reply = "✅ تم تحديث الرسالة."
            elif kind == "new_series":
                sid, title, topic = [p.strip() for p in txt.split("|", 2)]
                if sid in INDEX:
                    raise ValueError("المعرّف مستخدم بالفعل")
                INDEX[sid] = {"icon": "◆", "title": f"◆ {title}", "description": topic, "topic": topic, "period": "", "keywords": []}
                SERIES_DATA[sid] = []
                save_library()
                reply = "✅ أُضيفت السلسلة."
            elif kind == "new_msg":
                title, topic, content = [p.strip() for p in txt.split("|", 2)]
                sid = admin_state["sid"]
                if sid not in SERIES_DATA:
                    SERIES_DATA[sid] = []
                SERIES_DATA[sid].append({
                    "id": len(SERIES_DATA[sid]) + 1,
                    "title": title,
                    "topic": topic,
                    "text": content,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "length": len(content)
                })
                save_library()
                reply = "✅ أُضيفت الرسالة."
            else:
                raise ValueError("حالة إدارة غير معروفة")
            ctx.chat_data.pop("admin_state", None)
            await update.message.reply_text(reply, reply_markup=admin_menu_kb())
        except ValueError:
            await update.message.reply_text("⚠️ الصيغة غير صحيحة. أرسل بالشكل: (العنصر 1 | العنصر 2 | العنصر 3)")
        return

    mode = ctx.chat_data.get("mode", "")

    if mode == "inquiry":
        sid = ctx.chat_data.pop("isid")
        mid = ctx.chat_data.pop("imid")
        ctx.chat_data.pop("mode", "")
        info = INDEX.get(sid, {})
        u = update.effective_user
        inquiry = (
            f"📩 <b>استفسار جديد من قارئ</b>\n\n"
            f"👤 {esc(u.full_name)} (@{u.username or '-'})\n"
            f"🆔 ID: <code>{u.id}</code>\n"
            f"📚 {esc(info.get('title', sid))} — رسالة {mid}\n\n"
            f"❓ <b>نص الاستفسار:</b>\n{esc(txt)}"
        )
        if ADMIN_ID != 0:
            try:
                await ctx.bot.send_message(ADMIN_ID, inquiry, parse_mode="HTML")
                await update.message.reply_text(
                    "✅ <b>تم إرسال استفسارك بنجاح</b>\nسيتم الرد عليك في أقرب وقت.",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📖 العودة للرسالة", callback_data=f"r:{sid}:{mid}")],
                        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="m:main")]
                    ])
                )
            except Exception as e:
                logger.error(f"Inquiry failed: {e}")
                await update.message.reply_text("⚠️ تعذر إرسال الاستفسار للمشرف حالياً، يرجى المحاولة لاحقاً.")
        else:
            await update.message.reply_text("⚠️ لم يتم تعيين معرف المشرف ADMIN_ID في إعدادات البوت.")
        return

    if mode == "jump":
        sid = ctx.chat_data.pop("jsid")
        total = ctx.chat_data.pop("jtotal")
        ctx.chat_data.pop("mode", "")
        if txt.isdigit():
            mid = int(txt)
            if 1 <= mid <= total:
                await update.message.reply_text(
                    "⏩ جاري الانتقال...",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📖 افتح الرسالة", callback_data=f"r:{sid}:{mid}")]])
                )
            else:
                await update.message.reply_text(f"⚠️ الرقم يجب أن يكون بين 1 و {total}")
        else:
            await update.message.reply_text("⚠️ أرسل رقماً صحيحاً.")
        return

    if mode == "search_series":
        sid = ctx.chat_data.pop("ssid")
        ctx.chat_data.pop("mode", "")
        await do_search(update, ctx, sid, txt)
        return

    if mode == "search_global":
        ctx.chat_data.pop("mode", "")
        await do_search(update, ctx, None, txt)
        return

    # الوضع التلقائي: إذا أرسل المستخدم أي نص فسيتم البحث عنه في المكتبة
    await do_search(update, ctx, None, txt)

async def do_search(update, ctx, sid, query):
    ud = get_ud(ctx)
    ud["stats"]["searches"] += 1
    q = query.lower()
    results = []

    if sid:
        for msg in SERIES_DATA.get(sid, []):
            if q in msg.get("text", "").lower() or q in msg.get("title", "").lower():
                results.append((sid, msg))
        title = INDEX.get(sid, {}).get("title", sid)
    else:
        for s, messages in SERIES_DATA.items():
            for msg in messages:
                if q in msg.get("text", "").lower() or q in msg.get("title", "").lower():
                    results.append((s, msg))
        title = "المكتبة بالكامل"

    if not results:
        await update.message.reply_text(
            f'🔍 لم يتم العثور على نتائج لـ «{esc(query)}» في {esc(title)}.',
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="m:main")]])
        )
        return

    txt = f"🔍 تم العثور على <b>{len(results)}</b> نتيجة في <b>{esc(title)}</b> لـ «{esc(query)}»:\n"
    k = []
    for s, msg in results[:20]:
        icon = INDEX.get(s, {}).get('icon', '📄')
        preview = esc(msg.get("text", "")[:35].replace("\n", " ") + "...")
        k.append([InlineKeyboardButton(
            f"{icon} رسالة {msg['id']}: {preview}",
            callback_data=f"r:{s}:{msg['id']}"
        )])
    k.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="m:main")])
    await update.message.reply_text(txt, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(k))

def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TOKEN_HERE":
        logger.error("BOT_TOKEN is not set! Set the BOT_TOKEN environment variable or add it to .env file.")
        print("\n[!] خطأ: لم يتم ضبط BOT_TOKEN!")
        print("قم بإنشاء ملف .env وضع فيه:")
        print("BOT_TOKEN=توكن_البوت_الخاص_بك")
        print("ADMIN_ID=معرف_حسابك_الرقمي\n")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("continue", cont_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("whoami", whoami_cmd))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    
    logger.info("Bot is starting polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

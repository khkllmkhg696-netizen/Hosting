import asyncio
import os
import logging
from urllib.parse import urlparse
from datetime import datetime
import aiohttp
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

WAITING_FOR_URL = 1
WAITING_FOR_INTERVAL = 2
WAITING_FOR_RANDOM_RANGE = 3

user_data_store: dict = {}

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8716286773:AAFGjKJpQgQm3qNwr44cdI1e85ooBaO_RyU")
PORT = int(os.environ.get("PORT", 8080))
START_TIME = datetime.utcnow()


# ─────────────────────────────────────────
#   Access Control
# ─────────────────────────────────────────

ALLOWED_USERNAMES = {"f611f6", "c9aac"}
DEVELOPER_USERNAME = "c9aac"


def is_allowed(user) -> bool:
    if user is None:
        return False
    username = (user.username or "").lower().lstrip("@")
    return username in ALLOWED_USERNAMES


def denied_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👨‍💻 المطور", url=f"https://t.me/{DEVELOPER_USERNAME}")
    ]])


async def send_denied(message) -> None:
    await message.reply_text(
        "🔒 هذا البوت شخصي ولا يمكن استعماله بدون إذن.",
        reply_markup=denied_keyboard(),
    )


# ─────────────────────────────────────────
#   UptimeRobot Web Server
# ─────────────────────────────────────────

async def health_handler(request: web.Request) -> web.Response:
    uptime_seconds = int((datetime.utcnow() - START_TIME).total_seconds())
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    active_pings = sum(
        1
        for u in user_data_store.values()
        for entry in u.get("url_data", {}).values()
        if entry.get("running")
    )
    return web.Response(
        text=(
            f"✅ Bot is alive!\n"
            f"Uptime: {hours}h {minutes}m {seconds}s\n"
            f"Active users: {len(user_data_store)}\n"
            f"Running pings: {active_pings}"
        ),
        content_type="text/plain",
    )


async def start_web_server() -> None:
    app_web = web.Application()
    app_web.router.add_get("/", health_handler)
    app_web.router.add_get("/health", health_handler)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT} — UptimeRobot ready!")


# ─────────────────────────────────────────
#   Helper Functions
# ─────────────────────────────────────────

def is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return result.scheme in ("http", "https") and bool(result.netloc)
    except Exception:
        return False


def default_user_data() -> dict:
    return {
        "url_list": [],
        "url_data": {},
        "interval": 1,
        "random_mode": False,
        "random_min": None,
        "random_max": None,
    }


def default_url_entry() -> dict:
    return {
        "running": False,
        "task": None,
        "ping_message_id": None,
        "ping_count": 0,
        "random_index": 0,
    }


def get_url_at(user_id: int, idx: int):
    url_list = user_data_store.get(user_id, {}).get("url_list", [])
    if 0 <= idx < len(url_list):
        return url_list[idx]
    return None


# ─────────────────────────────────────────
#   Keyboards
# ─────────────────────────────────────────

def main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    data = user_data_store.get(user_id, {})
    url_list = data.get("url_list", [])
    url_data_map = data.get("url_data", {})
    interval = data.get("interval", 1)
    random_min = data.get("random_min")
    random_max = data.get("random_max")

    interval_label = f"⏱ الوقت: {interval} دقيقة"

    if random_min is not None and random_max is not None:
        random_label = f"🎲 الوقت العشوائي: {random_min}-{random_max} د"
    else:
        random_label = "🎲 الوقت العشوائي"

    keyboard = [
        [InlineKeyboardButton("➕ إضافة رابط", callback_data="add_url")],
        [InlineKeyboardButton(interval_label, callback_data="set_interval")],
        [InlineKeyboardButton(random_label, callback_data="random_menu")],
    ]

    for idx, url in enumerate(url_list):
        is_running = url_data_map.get(url, {}).get("running", False)
        status = "🟢" if is_running else "🔴"
        short_url = url[:32] + "..." if len(url) > 32 else url
        keyboard.append([InlineKeyboardButton(
            f"{status} {short_url}",
            callback_data=f"url_view_{idx}"
        )])

    return InlineKeyboardMarkup(keyboard)


def url_live_keyboard(idx: int, is_running: bool) -> InlineKeyboardMarkup:
    if is_running:
        keyboard = [[
            InlineKeyboardButton("⛔ إيقاف Ping", callback_data=f"stop_ping_{idx}"),
            InlineKeyboardButton("🗑 حذف الرابط", callback_data=f"remove_url_{idx}"),
        ]]
    else:
        keyboard = [
            [
                InlineKeyboardButton("▶️ بدء Ping", callback_data=f"start_ping_{idx}"),
                InlineKeyboardButton("🗑 حذف الرابط", callback_data=f"remove_url_{idx}"),
            ],
            [InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back_main")],
        ]
    return InlineKeyboardMarkup(keyboard)


def confirm_remove_keyboard(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ تأكيد الحذف", callback_data=f"confirm_remove_{idx}"),
        InlineKeyboardButton("❌ إلغاء", callback_data=f"cancel_remove_{idx}"),
    ]])


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="back_main")
    ]])


def random_keyboard(user_id: int) -> InlineKeyboardMarkup:
    data = user_data_store.get(user_id, {})
    random_mode = data.get("random_mode", False)
    random_min = data.get("random_min")
    random_max = data.get("random_max")

    toggle_label = "✅ تفعيل العشوائي" if random_mode else "❌ تفعيل العشوائي"

    if random_min is not None and random_max is not None:
        range_label = f"➕ إضافة الوقت: {random_min}-{random_max}"
    else:
        range_label = "➕ إضافة الوقت (مثال: 1-3)"

    keyboard = [
        [InlineKeyboardButton(toggle_label, callback_data="toggle_random")],
        [InlineKeyboardButton(range_label, callback_data="set_random_range")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ─────────────────────────────────────────
#   Telegram Handlers
# ─────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_allowed(user):
        await send_denied(update.message)
        return

    user_id = user.id
    if user_id not in user_data_store:
        user_data_store[user_id] = default_user_data()

    await update.message.reply_text(
        "👋 مرحباً! أنا بوت Ping.\n\n"
        "يمكنني زيارة عدة روابط بشكل تلقائي كل فترة زمنية تحددها.\n\n"
        "استخدم الأزرار أدناه للبدء:",
        reply_markup=main_keyboard(user_id),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    user = query.from_user
    if not is_allowed(user):
        await send_denied(query.message)
        return ConversationHandler.END

    user_id = user.id
    if user_id not in user_data_store:
        user_data_store[user_id] = default_user_data()

    data = query.data

    # ── القائمة الرئيسية ──────────────────
    if data == "add_url":
        await query.message.reply_text(
            "🔗 أرسل الرابط الذي تريد إضافته:\n\n"
            "مثال: https://example.com"
        )
        return WAITING_FOR_URL

    elif data == "set_interval":
        await query.message.reply_text(
            "⏱ أرسل الوقت بالدقائق (أقل وقت هو 1 دقيقة):\n\n"
            "مثال: 5"
        )
        return WAITING_FOR_INTERVAL

    elif data == "back_main":
        await query.message.reply_text(
            "📋 القائمة الرئيسية:",
            reply_markup=main_keyboard(user_id),
        )
        return ConversationHandler.END

    # ── الوقت العشوائي ────────────────────
    elif data == "random_menu":
        await query.message.reply_text(
            "🎲 إعدادات الوقت العشوائي:",
            reply_markup=random_keyboard(user_id),
        )
        return ConversationHandler.END

    elif data == "toggle_random":
        random_mode = user_data_store[user_id].get("random_mode", False)
        user_data_store[user_id]["random_mode"] = not random_mode
        status = "✅ مفعّل" if not random_mode else "❌ معطّل"
        await query.message.reply_text(
            f"تم تغيير الوضع العشوائي: {status}",
            reply_markup=random_keyboard(user_id),
        )
        return ConversationHandler.END

    elif data == "set_random_range":
        await query.message.reply_text(
            "⏱ أرسل النطاق الزمني بالصيغة التالية:\n\n"
            "الحد الأدنى-الحد الأقصى\n\n"
            "مثال: 1-3\n"
            "(يعني يزور الرابط مرة بعد 1 دقيقة، ومرة بعد 2، ومرة بعد 3، ثم يعيد)"
        )
        return WAITING_FOR_RANDOM_RANGE

    # ── عرض حالة رابط معيّن ──────────────
    elif data.startswith("url_view_"):
        idx = int(data.split("_")[-1])
        url = get_url_at(user_id, idx)
        if url is None:
            await query.message.reply_text("❌ الرابط غير موجود، ربما تم حذفه.")
            return ConversationHandler.END

        url_entry = user_data_store[user_id]["url_data"].get(url, {})
        is_running = url_entry.get("running", False)
        ping_count = url_entry.get("ping_count", 0)
        now = datetime.utcnow().strftime("%H:%M:%S")

        if is_running:
            text = (
                f"✅ Ping يعمل!\n"
                f"🔗 {url}\n"
                f"🔢 عدد المرات: {ping_count}\n"
                f"🕐 آخر تحديث: {now} UTC"
            )
        else:
            text = (
                f"⏸ Ping متوقف\n"
                f"🔗 {url}\n"
                f"🔢 عدد المرات: {ping_count}"
            )

        sent = await query.message.reply_text(
            text,
            reply_markup=url_live_keyboard(idx, is_running),
        )
        # هذه الرسالة تصبح الرسالة الحية للرابط
        user_data_store[user_id]["url_data"].setdefault(url, default_url_entry())
        user_data_store[user_id]["url_data"][url]["ping_message_id"] = sent.message_id
        return ConversationHandler.END

    # ── إيقاف Ping ────────────────────────
    elif data.startswith("stop_ping_"):
        idx = int(data.split("_")[-1])
        url = get_url_at(user_id, idx)
        if url is None:
            await query.message.reply_text("❌ الرابط غير موجود.")
            return ConversationHandler.END

        url_entry = user_data_store[user_id]["url_data"].get(url, {})
        url_entry["running"] = False
        task = url_entry.get("task")
        if task:
            task.cancel()
            url_entry["task"] = None

        ping_count = url_entry.get("ping_count", 0)
        await query.message.edit_text(
            f"⛔ تم إيقاف الـ Ping\n"
            f"🔗 {url}\n"
            f"🔢 عدد المرات: {ping_count}",
            reply_markup=url_live_keyboard(idx, False),
        )
        return ConversationHandler.END

    # ── بدء Ping ──────────────────────────
    elif data.startswith("start_ping_"):
        idx = int(data.split("_")[-1])
        url = get_url_at(user_id, idx)
        if url is None:
            await query.message.reply_text("❌ الرابط غير موجود.")
            return ConversationHandler.END

        url_entry = user_data_store[user_id]["url_data"].setdefault(url, default_url_entry())
        url_entry["running"] = True
        url_entry["ping_count"] = 0
        url_entry["random_index"] = 0
        # استخدام نفس الرسالة الحالية كرسالة حية
        url_entry["ping_message_id"] = query.message.message_id

        task = asyncio.create_task(ping_loop(user_id, url, context))
        url_entry["task"] = task

        await query.message.edit_text(
            f"🔄 جاري البدء...\n🔗 {url}",
            reply_markup=url_live_keyboard(idx, True),
        )
        return ConversationHandler.END

    # ── طلب حذف رابط ─────────────────────
    elif data.startswith("remove_url_"):
        idx = int(data.split("_")[-1])
        url = get_url_at(user_id, idx)
        if url is None:
            await query.message.edit_text(
                "❌ الرابط غير موجود.",
                reply_markup=back_to_menu_keyboard(),
            )
            return ConversationHandler.END

        await query.message.edit_text(
            f"⚠️ هل أنت متأكد أنك تريد إزالة الرابط؟\n\n🔗 {url}",
            reply_markup=confirm_remove_keyboard(idx),
        )
        return ConversationHandler.END

    # ── تأكيد الحذف ──────────────────────
    elif data.startswith("confirm_remove_"):
        idx = int(data.split("_")[-1])
        url = get_url_at(user_id, idx)

        if url is None:
            await query.message.edit_text(
                "✅ الرابط غير موجود أو تم حذفه مسبقاً.",
                reply_markup=back_to_menu_keyboard(),
            )
            return ConversationHandler.END

        # إيقاف الـ Ping إن كان يعمل
        url_entry = user_data_store[user_id]["url_data"].get(url, {})
        if url_entry.get("running"):
            url_entry["running"] = False
            task = url_entry.get("task")
            if task:
                task.cancel()

        # حذف الرابط
        user_data_store[user_id]["url_list"].remove(url)
        user_data_store[user_id]["url_data"].pop(url, None)

        await query.message.edit_text(
            f"✅ تم حذف الرابط بنجاح.\n🔗 {url}",
            reply_markup=back_to_menu_keyboard(),
        )
        return ConversationHandler.END

    # ── إلغاء الحذف ──────────────────────
    elif data.startswith("cancel_remove_"):
        idx = int(data.split("_")[-1])
        url = get_url_at(user_id, idx)
        if url is None:
            await query.message.edit_text(
                "❌ الرابط غير موجود.",
                reply_markup=back_to_menu_keyboard(),
            )
            return ConversationHandler.END

        url_entry = user_data_store[user_id]["url_data"].get(url, {})
        is_running = url_entry.get("running", False)
        ping_count = url_entry.get("ping_count", 0)
        now = datetime.utcnow().strftime("%H:%M:%S")

        if is_running:
            text = (
                f"✅ Ping يعمل!\n"
                f"🔗 {url}\n"
                f"🔢 عدد المرات: {ping_count}\n"
                f"🕐 آخر تحديث: {now} UTC"
            )
        else:
            text = (
                f"⏸ Ping متوقف\n"
                f"🔗 {url}\n"
                f"🔢 عدد المرات: {ping_count}"
            )

        await query.message.edit_text(
            text,
            reply_markup=url_live_keyboard(idx, is_running),
        )
        return ConversationHandler.END

    return ConversationHandler.END


async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not is_allowed(user):
        await send_denied(update.message)
        return ConversationHandler.END

    user_id = user.id
    url = update.message.text.strip()

    if not is_valid_url(url):
        await update.message.reply_text(
            "❌ الرابط غير صحيح! تأكد أنه يبدأ بـ http:// أو https://\n\nأرسل الرابط مرة أخرى:"
        )
        return WAITING_FOR_URL

    if user_id not in user_data_store:
        user_data_store[user_id] = default_user_data()

    url_list = user_data_store[user_id]["url_list"]
    if url in url_list:
        await update.message.reply_text(
            "⚠️ هذا الرابط موجود مسبقاً في قائمتك!",
            reply_markup=main_keyboard(user_id),
        )
        return ConversationHandler.END

    # إضافة الرابط وبدء الـ Ping فوراً
    url_list.append(url)
    url_entry = default_url_entry()
    url_entry["running"] = True
    user_data_store[user_id]["url_data"][url] = url_entry

    task = asyncio.create_task(ping_loop(user_id, url, context))
    url_entry["task"] = task

    interval = user_data_store[user_id].get("interval", 1)
    random_mode = user_data_store[user_id].get("random_mode", False)
    random_min = user_data_store[user_id].get("random_min")
    random_max = user_data_store[user_id].get("random_max")

    if random_mode and random_min is not None and random_max is not None:
        mode_text = f"🎲 وضع عشوائي: {random_min}-{random_max} دقيقة"
    else:
        mode_text = f"⏱ كل {interval} دقيقة"

    await update.message.reply_text(
        f"✅ تم إضافة الرابط وبدأ الـ Ping!\n\n"
        f"🔗 {url}\n"
        f"{mode_text}",
        reply_markup=main_keyboard(user_id),
    )
    return ConversationHandler.END


async def receive_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not is_allowed(user):
        await send_denied(update.message)
        return ConversationHandler.END

    user_id = user.id
    text = update.message.text.strip()

    try:
        interval = int(text)
        if interval < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ الرقم غير صحيح! يجب أن يكون رقماً صحيحاً وأكبر من أو يساوي 1.\n\nأرسل الوقت مرة أخرى:"
        )
        return WAITING_FOR_INTERVAL

    if user_id not in user_data_store:
        user_data_store[user_id] = default_user_data()

    user_data_store[user_id]["interval"] = interval

    await update.message.reply_text(
        f"✅ تم حفظ الوقت: {interval} دقيقة\n\n"
        "⚠️ سيُطبق هذا الوقت على جميع الروابط النشطة والجديدة.",
        reply_markup=main_keyboard(user_id),
    )
    return ConversationHandler.END


async def receive_random_range(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not is_allowed(user):
        await send_denied(update.message)
        return ConversationHandler.END

    user_id = user.id
    text = update.message.text.strip()

    try:
        parts = text.split("-")
        if len(parts) != 2:
            raise ValueError
        min_val = int(parts[0].strip())
        max_val = int(parts[1].strip())
        if min_val < 1 or max_val < 1 or min_val >= max_val:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ الصيغة غير صحيحة!\n\n"
            "يجب أن تكون بالشكل: رقم-رقم\n"
            "مثال: 1-3\n\n"
            "• يجب أن يكون الرقم الأول أصغر من الثاني\n"
            "• يجب أن تكون الأرقام موجبة وأكبر من صفر\n\n"
            "أرسل النطاق مرة أخرى:"
        )
        return WAITING_FOR_RANDOM_RANGE

    if user_id not in user_data_store:
        user_data_store[user_id] = default_user_data()

    user_data_store[user_id]["random_min"] = min_val
    user_data_store[user_id]["random_max"] = max_val

    await update.message.reply_text(
        f"✅ تم حفظ النطاق: {min_val}-{max_val} دقيقة\n\n"
        f"سيتم زيارة الرابط بالتسلسل: {min_val} ← {' ← '.join(str(i) for i in range(min_val + 1, max_val + 1))} ← {min_val} ...",
        reply_markup=random_keyboard(user_id),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not is_allowed(user):
        await send_denied(update.message)
        return ConversationHandler.END
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END


# ─────────────────────────────────────────
#   Ping Loop
# ─────────────────────────────────────────

async def ping_loop(user_id: int, url: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    while True:
        user = user_data_store.get(user_id)
        if not user:
            break
        url_entry = user.get("url_data", {}).get(url)
        if not url_entry or not url_entry.get("running", False):
            break

        url_list = user.get("url_list", [])
        idx = url_list.index(url) if url in url_list else 0

        ping_count = url_entry.get("ping_count", 0) + 1
        url_entry["ping_count"] = ping_count

        random_mode = user.get("random_mode", False)
        random_min = user.get("random_min")
        random_max = user.get("random_max")
        random_index = url_entry.get("random_index", 0)

        if random_mode and random_min is not None and random_max is not None:
            cycle_length = random_max - random_min + 1
            current_interval = random_min + (random_index % cycle_length)
            url_entry["random_index"] = random_index + 1
            interval_label = f"🎲 {current_interval} دقيقة (عشوائي {random_min}-{random_max})"
        else:
            current_interval = user.get("interval", 1)
            interval_label = f"⏱ كل {current_interval} دقيقة"

        now = datetime.utcnow().strftime("%H:%M:%S")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    status = response.status
                    msg = (
                        f"✅ Ping ناجح!\n"
                        f"🔗 {url}\n"
                        f"📊 الحالة: {status}\n"
                        f"🔢 عدد المرات: {ping_count}\n"
                        f"🕐 آخر تحديث: {now} UTC\n"
                        f"{interval_label}"
                    )
        except asyncio.CancelledError:
            return
        except Exception as e:
            msg = (
                f"❌ فشل الـ Ping!\n"
                f"🔗 {url}\n"
                f"⚠️ الخطأ: {str(e)}\n"
                f"🔢 عدد المرات: {ping_count}\n"
                f"🕐 آخر تحديث: {now} UTC\n"
                f"{interval_label}"
            )

        reply_markup = url_live_keyboard(idx, True)

        try:
            ping_message_id = url_entry.get("ping_message_id")
            if ping_message_id is None:
                sent = await context.bot.send_message(
                    chat_id=user_id, text=msg, reply_markup=reply_markup
                )
                url_entry["ping_message_id"] = sent.message_id
            else:
                await context.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=ping_message_id,
                    text=msg,
                    reply_markup=reply_markup,
                )
        except asyncio.CancelledError:
            return
        except Exception:
            try:
                sent = await context.bot.send_message(
                    chat_id=user_id, text=msg, reply_markup=reply_markup
                )
                url_entry["ping_message_id"] = sent.message_id
            except Exception:
                pass

        try:
            await asyncio.sleep(current_interval * 60)
        except asyncio.CancelledError:
            return


# ─────────────────────────────────────────
#   Main Entry Point
# ─────────────────────────────────────────

async def run_bot() -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN غير موجود في المتغيرات البيئية!")

    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler)],
        states={
            WAITING_FOR_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url)
            ],
            WAITING_FOR_INTERVAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_interval)
            ],
            WAITING_FOR_RANDOM_RANGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_random_range)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)

    await start_web_server()

    logger.info("البوت يعمل الآن...")
    async with app:
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(run_bot())

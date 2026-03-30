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
#   UptimeRobot Web Server
# ─────────────────────────────────────────

async def health_handler(request: web.Request) -> web.Response:
    uptime_seconds = int((datetime.utcnow() - START_TIME).total_seconds())
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return web.Response(
        text=(
            f"✅ Bot is alive!\n"
            f"Uptime: {hours}h {minutes}m {seconds}s\n"
            f"Active users: {len(user_data_store)}\n"
            f"Running pings: {sum(1 for u in user_data_store.values() if u.get('running'))}"
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
        "url": "",
        "interval": 1,
        "running": False,
        "task": None,
        "ping_message_id": None,
        "ping_count": 0,
        "random_mode": False,
        "random_min": None,
        "random_max": None,
        "random_index": 0,
    }


def main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    data = user_data_store.get(user_id, {})
    url = data.get("url", "")
    interval = data.get("interval", 1)
    is_running = data.get("running", False)
    random_mode = data.get("random_mode", False)
    random_min = data.get("random_min")
    random_max = data.get("random_max")

    if url:
        url_label = f"🔗 الرابط: {url[:28]}..." if len(url) > 28 else f"🔗 الرابط: {url}"
    else:
        url_label = "➕ إضافة رابط"

    interval_label = f"⏱ الوقت: {interval} دقيقة"
    ping_label = "⛔ إيقاف Ping" if is_running else "▶️ بدء Ping"

    if random_min is not None and random_max is not None:
        random_label = f"🎲 الوقت العشوائي: {random_min}-{random_max} د"
    else:
        random_label = "🎲 الوقت العشوائي"

    keyboard = [
        [InlineKeyboardButton(url_label, callback_data="add_url")],
        [InlineKeyboardButton(interval_label, callback_data="set_interval")],
        [InlineKeyboardButton(random_label, callback_data="random_menu")],
        [InlineKeyboardButton(ping_label, callback_data="toggle_ping")],
    ]
    return InlineKeyboardMarkup(keyboard)


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
    user_id = update.effective_user.id
    if user_id not in user_data_store:
        user_data_store[user_id] = default_user_data()

    await update.message.reply_text(
        "👋 مرحباً! أنا بوت Ping.\n\n"
        "يمكنني زيارة أي رابط بشكل تلقائي كل فترة زمنية تحددها.\n\n"
        "استخدم الأزرار أدناه للبدء:",
        reply_markup=main_keyboard(user_id),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id not in user_data_store:
        user_data_store[user_id] = default_user_data()

    data = query.data

    # ── القائمة الرئيسية ──────────────────
    if data == "add_url":
        await query.message.reply_text(
            "🔗 أرسل الرابط الذي تريد زيارته:\n\n"
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
        await query.message.edit_text(
            "استخدم الأزرار أدناه:",
            reply_markup=main_keyboard(user_id),
        )
        return ConversationHandler.END

    elif data == "toggle_ping":
        is_running = user_data_store[user_id].get("running", False)
        url = user_data_store[user_id].get("url", "")

        if not is_running:
            if not url:
                await query.message.reply_text(
                    "⚠️ يجب إضافة رابط أولاً!",
                    reply_markup=main_keyboard(user_id),
                )
                return ConversationHandler.END

            random_mode = user_data_store[user_id].get("random_mode", False)
            random_min = user_data_store[user_id].get("random_min")
            random_max = user_data_store[user_id].get("random_max")

            if random_mode and (random_min is None or random_max is None):
                await query.message.reply_text(
                    "⚠️ الوضع العشوائي مفعّل لكن لم تحدد النطاق الزمني!\n"
                    "اذهب لـ 🎲 الوقت العشوائي وأضف الوقت أولاً.",
                    reply_markup=main_keyboard(user_id),
                )
                return ConversationHandler.END

            user_data_store[user_id]["running"] = True
            user_data_store[user_id]["ping_message_id"] = None
            user_data_store[user_id]["ping_count"] = 0
            user_data_store[user_id]["random_index"] = 0
            task = asyncio.create_task(ping_loop(user_id, context))
            user_data_store[user_id]["task"] = task

            if random_mode:
                mode_text = f"🎲 وضع عشوائي: {random_min}-{random_max} دقيقة"
            else:
                interval = user_data_store[user_id].get("interval", 1)
                mode_text = f"⏱ كل {interval} دقيقة"

            await query.message.reply_text(
                f"✅ بدأ الـ Ping!\n\n"
                f"🔗 الرابط: {url}\n"
                f"{mode_text}",
                reply_markup=main_keyboard(user_id),
            )
        else:
            user_data_store[user_id]["running"] = False
            task = user_data_store[user_id].get("task")
            if task:
                task.cancel()
                user_data_store[user_id]["task"] = None
            user_data_store[user_id]["ping_message_id"] = None
            user_data_store[user_id]["ping_count"] = 0
            user_data_store[user_id]["random_index"] = 0

            await query.message.reply_text(
                "⛔ تم إيقاف الـ Ping.",
                reply_markup=main_keyboard(user_id),
            )
        return ConversationHandler.END

    # ── قائمة الوقت العشوائي ─────────────
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

    return ConversationHandler.END


async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    url = update.message.text.strip()

    if not is_valid_url(url):
        await update.message.reply_text(
            "❌ الرابط غير صحيح! تأكد أنه يبدأ بـ http:// أو https://\n\nأرسل الرابط مرة أخرى:"
        )
        return WAITING_FOR_URL

    if user_id not in user_data_store:
        user_data_store[user_id] = default_user_data()

    was_running = user_data_store[user_id].get("running", False)
    if was_running:
        user_data_store[user_id]["running"] = False
        task = user_data_store[user_id].get("task")
        if task:
            task.cancel()
            user_data_store[user_id]["task"] = None
        user_data_store[user_id]["ping_message_id"] = None
        user_data_store[user_id]["ping_count"] = 0

    user_data_store[user_id]["url"] = url

    await update.message.reply_text(
        f"✅ تم حفظ الرابط:\n{url}",
        reply_markup=main_keyboard(user_id),
    )
    return ConversationHandler.END


async def receive_interval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
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

    was_running = user_data_store[user_id].get("running", False)
    if was_running:
        user_data_store[user_id]["running"] = False
        task = user_data_store[user_id].get("task")
        if task:
            task.cancel()
            user_data_store[user_id]["task"] = None
        user_data_store[user_id]["ping_message_id"] = None
        user_data_store[user_id]["ping_count"] = 0

    user_data_store[user_id]["interval"] = interval

    if was_running:
        await update.message.reply_text(
            f"✅ تم تغيير الوقت إلى {interval} دقيقة.\n"
            "⚠️ تم إيقاف الـ Ping، اضغط على بدء Ping مرة أخرى.",
            reply_markup=main_keyboard(user_id),
        )
    else:
        await update.message.reply_text(
            f"✅ تم حفظ الوقت: {interval} دقيقة",
            reply_markup=main_keyboard(user_id),
        )
    return ConversationHandler.END


async def receive_random_range(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
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
    user_data_store[user_id]["random_index"] = 0

    await update.message.reply_text(
        f"✅ تم حفظ النطاق: {min_val}-{max_val} دقيقة\n\n"
        f"سيتم زيارة الرابط بالتسلسل: {min_val} ← {' ← '.join(str(i) for i in range(min_val+1, max_val+1))} ← {min_val} ...",
        reply_markup=random_keyboard(user_id),
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END


# ─────────────────────────────────────────
#   Ping Loop
# ─────────────────────────────────────────

async def ping_loop(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    while user_data_store.get(user_id, {}).get("running", False):
        url = user_data_store[user_id].get("url", "")
        ping_count = user_data_store[user_id].get("ping_count", 0) + 1
        user_data_store[user_id]["ping_count"] = ping_count

        random_mode = user_data_store[user_id].get("random_mode", False)
        random_min = user_data_store[user_id].get("random_min", 1)
        random_max = user_data_store[user_id].get("random_max", 1)
        random_index = user_data_store[user_id].get("random_index", 0)

        if random_mode and random_min is not None and random_max is not None:
            cycle_length = random_max - random_min + 1
            current_interval = random_min + (random_index % cycle_length)
            user_data_store[user_id]["random_index"] = random_index + 1
            interval_label = f"🎲 {current_interval} دقيقة (عشوائي {random_min}-{random_max})"
        else:
            current_interval = user_data_store[user_id].get("interval", 1)
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

        try:
            ping_message_id = user_data_store[user_id].get("ping_message_id")

            if ping_message_id is None:
                sent = await context.bot.send_message(chat_id=user_id, text=msg)
                user_data_store[user_id]["ping_message_id"] = sent.message_id
            else:
                await context.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=ping_message_id,
                    text=msg,
                )
        except Exception:
            try:
                sent = await context.bot.send_message(chat_id=user_id, text=msg)
                user_data_store[user_id]["ping_message_id"] = sent.message_id
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

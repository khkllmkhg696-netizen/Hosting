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


def main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    data = user_data_store.get(user_id, {})
    url = data.get("url", "")
    interval = data.get("interval", 1)
    is_running = data.get("running", False)

    if url:
        url_label = f"🔗 الرابط: {url[:28]}..." if len(url) > 28 else f"🔗 الرابط: {url}"
    else:
        url_label = "➕ إضافة رابط"

    interval_label = f"⏱ الوقت: {interval} دقيقة"
    ping_label = "⛔ إيقاف Ping" if is_running else "▶️ بدء Ping"

    keyboard = [
        [InlineKeyboardButton(url_label, callback_data="add_url")],
        [InlineKeyboardButton(interval_label, callback_data="set_interval")],
        [InlineKeyboardButton(ping_label, callback_data="toggle_ping")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ─────────────────────────────────────────
#   Telegram Handlers
# ─────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in user_data_store:
        user_data_store[user_id] = {
            "url": "",
            "interval": 1,
            "running": False,
            "task": None,
        }

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
        user_data_store[user_id] = {
            "url": "",
            "interval": 1,
            "running": False,
            "task": None,
        }

    data = query.data

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

            user_data_store[user_id]["running"] = True
            task = asyncio.create_task(ping_loop(user_id, context))
            user_data_store[user_id]["task"] = task

            interval = user_data_store[user_id].get("interval", 1)
            await query.message.reply_text(
                f"✅ بدأ الـ Ping!\n\n"
                f"🔗 الرابط: {url}\n"
                f"⏱ كل {interval} دقيقة",
                reply_markup=main_keyboard(user_id),
            )
        else:
            user_data_store[user_id]["running"] = False
            task = user_data_store[user_id].get("task")
            if task:
                task.cancel()
                user_data_store[user_id]["task"] = None

            await query.message.reply_text(
                "⛔ تم إيقاف الـ Ping.",
                reply_markup=main_keyboard(user_id),
            )

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
        user_data_store[user_id] = {"url": "", "interval": 1, "running": False, "task": None}

    was_running = user_data_store[user_id].get("running", False)
    if was_running:
        user_data_store[user_id]["running"] = False
        task = user_data_store[user_id].get("task")
        if task:
            task.cancel()
            user_data_store[user_id]["task"] = None

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
        user_data_store[user_id] = {"url": "", "interval": 1, "running": False, "task": None}

    was_running = user_data_store[user_id].get("running", False)
    if was_running:
        user_data_store[user_id]["running"] = False
        task = user_data_store[user_id].get("task")
        if task:
            task.cancel()
            user_data_store[user_id]["task"] = None

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


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("تم الإلغاء.")
    return ConversationHandler.END


# ─────────────────────────────────────────
#   Ping Loop
# ─────────────────────────────────────────

async def ping_loop(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    while user_data_store.get(user_id, {}).get("running", False):
        url = user_data_store[user_id].get("url", "")
        interval = user_data_store[user_id].get("interval", 1)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as response:
                    status = response.status
                    msg = (
                        f"✅ Ping ناجح!\n"
                        f"🔗 {url}\n"
                        f"📊 الحالة: {status}"
                    )
        except asyncio.CancelledError:
            return
        except Exception as e:
            msg = (
                f"❌ فشل الـ Ping!\n"
                f"🔗 {url}\n"
                f"⚠️ الخطأ: {str(e)}"
            )

        try:
            await context.bot.send_message(chat_id=user_id, text=msg)
        except Exception:
            pass

        try:
            await asyncio.sleep(interval * 60)
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

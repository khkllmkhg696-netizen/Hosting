import os
import json
import asyncio
import tempfile
import base64
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

BOT_TOKEN = "8718244006:AAHcgec0NRj_5JMrKilj1MHDnWtDwFMACEA"
RENDER_API_KEY = "rnd_OdDPHfiuKsDkOAilf5J7fqLqOygE"
GITHUB_TOKEN = "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"
GITHUB_USERNAME = "hosting-bot-user"
GITHUB_API = "https://api.github.com"
RENDER_API = "https://api.render.com/v1"

WAITING_BOT_FILE = 1
WAITING_REQUIREMENTS = 2

user_data_store: dict = {}


def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("📤 رفع ملف البوت", callback_data="upload_bot_file")],
        [InlineKeyboardButton("🚀 رفع البوت للاستضافة", callback_data="deploy_bot")],
        [InlineKeyboardButton("🤖 البوتات المرفوعة", callback_data="list_bots")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data_store:
        user_data_store[user_id] = {"bots": [], "pending": {}}

    await update.message.reply_text(
        "أهلا بك في بوت الاستضافة لول 👋\n\nاختر من القائمة أدناه:",
        reply_markup=get_main_keyboard(),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id not in user_data_store:
        user_data_store[user_id] = {"bots": [], "pending": {}}

    if query.data == "upload_bot_file":
        await query.message.reply_text(
            "📎 أرسل ملف البوت (يجب أن يكون ملف Python بامتداد .py):"
        )
        context.user_data["state"] = WAITING_BOT_FILE

    elif query.data == "deploy_bot":
        pending = user_data_store[user_id].get("pending", {})
        if not pending.get("bot_file") or not pending.get("requirements"):
            await query.message.reply_text(
                "⚠️ يجب عليك رفع ملف البوت وملف المتطلبات أولاً قبل الرفع للاستضافة.",
                reply_markup=get_main_keyboard(),
            )
            return
        await query.message.reply_text("⏳ جاري رفع البوت للاستضافة... الرجاء الانتظار")
        await deploy_to_render(query, user_id, context)

    elif query.data == "list_bots":
        bots = user_data_store.get(user_id, {}).get("bots", [])
        if not bots:
            await query.message.reply_text(
                "لا توجد بوتات مرفوعة حتى الآن.",
                reply_markup=get_main_keyboard(),
            )
        else:
            text = "🤖 *البوتات المرفوعة:*\n\n"
            for i, bot in enumerate(bots, 1):
                text += f"{i}. `{bot['name']}`\n   🔗 {bot.get('url', 'غير متاح')}\n\n"
            await query.message.reply_text(
                text,
                parse_mode="Markdown",
                reply_markup=get_main_keyboard(),
            )

    elif query.data == "remove_file":
        user_data_store[user_id]["pending"] = {}
        await query.message.reply_text(
            "🗑️ تم حذف الملفات المرفوعة بنجاح.",
            reply_markup=get_main_keyboard(),
        )


async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get("state")

    if user_id not in user_data_store:
        user_data_store[user_id] = {"bots": [], "pending": {}}

    document = update.message.document
    if not document:
        await update.message.reply_text("الرجاء إرسال ملف.")
        return

    if state == WAITING_BOT_FILE:
        if not document.file_name.endswith(".py"):
            await update.message.reply_text(
                "⚠️ عذرا، بوت يعمل لاستضافة Python فقط.\nالرجاء إرسال ملف بامتداد .py"
            )
            return

        file = await document.get_file()
        file_bytes = await file.download_as_bytearray()
        file_content = bytes(file_bytes).decode("utf-8")

        user_data_store[user_id]["pending"]["bot_file"] = {
            "name": document.file_name,
            "content": file_content,
        }

        await update.message.reply_text(
            f"✅ تم استلام ملف البوت: `{document.file_name}`\n\n"
            "الآن أرسل ملف المتطلبات (requirements.txt):",
            parse_mode="Markdown",
        )
        context.user_data["state"] = WAITING_REQUIREMENTS

    elif state == WAITING_REQUIREMENTS:
        if document.file_name != "requirements.txt":
            await update.message.reply_text(
                "⚠️ الرجاء إرسال ملف المتطلبات باسم `requirements.txt`",
                parse_mode="Markdown",
            )
            return

        file = await document.get_file()
        file_bytes = await file.download_as_bytearray()
        file_content = bytes(file_bytes).decode("utf-8")

        user_data_store[user_id]["pending"]["requirements"] = {
            "name": document.file_name,
            "content": file_content,
        }

        context.user_data["state"] = None

        keyboard = [
            [InlineKeyboardButton("🚀 رفع البوت للاستضافة", callback_data="deploy_bot")],
            [InlineKeyboardButton("🗑️ إزالة الملفات", callback_data="remove_file")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")],
        ]

        await update.message.reply_text(
            "✅ تم استلام ملف المتطلبات بنجاح!\n\n"
            "الملفات جاهزة للرفع. اختر ما تريد:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    else:
        await update.message.reply_text(
            "استخدم القائمة للتنقل:",
            reply_markup=get_main_keyboard(),
        )


async def get_github_username(session: aiohttp.ClientSession) -> str:
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    async with session.get(f"{GITHUB_API}/user", headers=headers) as resp:
        data = await resp.json()
        return data.get("login", "unknown")


async def create_github_repo(session: aiohttp.ClientSession, repo_name: str) -> dict:
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    payload = {
        "name": repo_name,
        "private": True,
        "auto_init": False,
        "description": "Bot hosted via Telegram Hosting Bot",
    }
    async with session.post(
        f"{GITHUB_API}/user/repos", headers=headers, json=payload
    ) as resp:
        data = await resp.json()
        return data


async def push_file_to_github(
    session: aiohttp.ClientSession,
    username: str,
    repo_name: str,
    file_path: str,
    file_content: str,
    message: str,
) -> dict:
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    encoded = base64.b64encode(file_content.encode("utf-8")).decode("utf-8")
    payload = {
        "message": message,
        "content": encoded,
    }
    url = f"{GITHUB_API}/repos/{username}/{repo_name}/contents/{file_path}"
    async with session.put(url, headers=headers, json=payload) as resp:
        data = await resp.json()
        return data


async def create_render_service(
    session: aiohttp.ClientSession,
    repo_url: str,
    service_name: str,
    bot_filename: str,
) -> dict:
    headers = {
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {
        "type": "web_service",
        "name": service_name,
        "ownerId": None,
        "repo": repo_url,
        "branch": "main",
        "runtime": "python",
        "buildCommand": "pip install -r requirements.txt",
        "startCommand": f"python {bot_filename}",
        "envVars": [],
        "plan": "free",
        "autoDeploy": "yes",
    }
    async with session.post(
        f"{RENDER_API}/services", headers=headers, json=payload
    ) as resp:
        data = await resp.json()
        return data


async def deploy_to_render(query, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    pending = user_data_store[user_id]["pending"]
    bot_file = pending["bot_file"]
    requirements = pending["requirements"]

    import re
    import time

    safe_name = re.sub(r"[^a-z0-9-]", "-", bot_file["name"].replace(".py", "").lower())
    repo_name = f"tgbot-{safe_name}-{int(time.time())}"
    service_name = repo_name

    try:
        async with aiohttp.ClientSession() as session:
            username = await get_github_username(session)
            repo_data = await create_github_repo(session, repo_name)

            if "id" not in repo_data:
                error_msg = repo_data.get("message", "خطأ غير معروف")
                await query.message.reply_text(
                    f"❌ فشلت العملية\n\nالسبب: فشل إنشاء المستودع على GitHub\nالخطأ: {error_msg}",
                    reply_markup=get_main_keyboard(),
                )
                return

            await asyncio.sleep(2)

            await push_file_to_github(
                session,
                username,
                repo_name,
                bot_file["name"],
                bot_file["content"],
                "Add bot file",
            )
            await push_file_to_github(
                session,
                username,
                repo_name,
                "requirements.txt",
                requirements["content"],
                "Add requirements",
            )

            repo_url = f"https://github.com/{username}/{repo_name}"
            render_data = await create_render_service(
                session, repo_url, service_name, bot_file["name"]
            )

            if "service" in render_data:
                service = render_data["service"]
                service_url = service.get("serviceDetails", {}).get("url", "جاري التفعيل...")
                service_id = service.get("id", "")

                user_data_store[user_id]["bots"].append(
                    {
                        "name": service_name,
                        "url": service_url,
                        "service_id": service_id,
                        "repo": repo_url,
                    }
                )
                user_data_store[user_id]["pending"] = {}

                await query.message.reply_text(
                    f"✅ *تم رفع البوت بنجاح!*\n\n"
                    f"📦 المستودع: {repo_url}\n"
                    f"🚀 الخدمة: `{service_name}`\n"
                    f"⏳ البوت قيد التشغيل على Render (قد يستغرق بضع دقائق للإطلاق).",
                    parse_mode="Markdown",
                    reply_markup=get_main_keyboard(),
                )
            elif "id" in render_data:
                service_url = render_data.get("serviceDetails", {}).get("url", "جاري التفعيل...")
                service_id = render_data.get("id", "")

                user_data_store[user_id]["bots"].append(
                    {
                        "name": service_name,
                        "url": service_url,
                        "service_id": service_id,
                        "repo": repo_url,
                    }
                )
                user_data_store[user_id]["pending"] = {}

                await query.message.reply_text(
                    f"✅ *تم رفع البوت بنجاح!*\n\n"
                    f"📦 المستودع: {repo_url}\n"
                    f"🚀 الخدمة: `{service_name}`\n"
                    f"⏳ البوت قيد التشغيل على Render (قد يستغرق بضع دقائق للإطلاق).",
                    parse_mode="Markdown",
                    reply_markup=get_main_keyboard(),
                )
            else:
                error_msg = render_data.get("message", str(render_data))
                await query.message.reply_text(
                    f"❌ فشلت العملية\n\nالسبب: فشل إنشاء الخدمة على Render\nالخطأ: {error_msg}",
                    reply_markup=get_main_keyboard(),
                )

    except aiohttp.ClientError as e:
        await query.message.reply_text(
            f"❌ فشلت العملية\n\nالسبب: خطأ في الاتصال بالشبكة\nالخطأ: {str(e)}",
            reply_markup=get_main_keyboard(),
        )
    except Exception as e:
        await query.message.reply_text(
            f"❌ فشلت العملية\n\nالسبب: {str(e)}",
            reply_markup=get_main_keyboard(),
        )


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "القائمة الرئيسية:",
        reply_markup=get_main_keyboard(),
    )


async def ping_handler(request):
    return web.Response(text="✅ Bot is alive!", content_type="text/plain")


async def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    web_app = web.Application()
    web_app.router.add_get("/", ping_handler)
    web_app.router.add_get("/ping", ping_handler)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Keep-alive web server running on port {port}")
    print(f"UptimeRobot / Ping URL: http://YOUR_RENDER_URL:{port}/ping")


async def run_bot():
    tg_app = Application.builder().token(BOT_TOKEN).build()

    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(
        CallbackQueryHandler(main_menu_callback, pattern="^main_menu$")
    )
    tg_app.add_handler(CallbackQueryHandler(button_handler))
    tg_app.add_handler(MessageHandler(filters.Document.ALL, file_handler))

    print("Telegram bot is running...")
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling()
    await asyncio.Event().wait()


async def main():
    await asyncio.gather(
        run_web_server(),
        run_bot(),
    )


if __name__ == "__main__":
    asyncio.run(main())

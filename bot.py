import logging
import os
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN      = os.environ["BOT_TOKEN"]
GROUP_CHAT_ID  = int(os.environ["GROUP_CHAT_ID"])
GROUP_TOPIC_ID = int(os.environ["GROUP_TOPIC_ID"])
SHEET_ID       = os.environ["SHEET_ID"]
GOOGLE_CREDS   = os.environ["GOOGLE_CREDS"]

DEPT, REQ_TYPE, ORDER_NUM, PRODUCT, DETAILS, DEADLINE, CONFIRM = range(7)

def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDS)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).sheet1

def save_to_sheet(data):
    try:
        sheet = get_sheet()
        if not sheet.row_values(1):
            sheet.append_row([
                "request_id","created_at","department","request_type",
                "order_num","product","details","deadline",
                "status","manager_comment","updated_at",
                "chat_id_master","message_id_group"
            ])
        sheet.append_row([
            data["request_id"], data["created_at"], data["department"],
            data["request_type"], data["order_num"], data["product"],
            data["details"], data["deadline"], "Нова", "", "",
            str(data["chat_id"]), str(data.get("message_id_group",""))
        ])
        logger.info(f"Saved to sheet: {data['request_id']}")
    except Exception as e:
        logger.error(f"Sheet error: {e}")

_counter_file = "/app/counter.txt"

def next_id():
    try:
        with open(_counter_file) as f:
            n = int(f.read().strip()) + 1
    except:
        n = 1
    with open(_counter_file, "w") as f:
        f.write(str(n))
    return f"REQ-{n:04d}"

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    kb = [
        [InlineKeyboardButton("🔨 Майстри", callback_data="dept_Майстри")],
        [InlineKeyboardButton("🏭 Виробництво", callback_data="dept_Виробництво")],
        [InlineKeyboardButton("💼 Комерційний", callback_data="dept_Комерційний")],
    ]
    await update.message.reply_text(
        "👋 Вітаю!\n\nОберіть ваш відділ:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return DEPT

async def step_dept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    dept = q.data.replace("dept_", "")
    context.user_data["department"] = dept
    logger.info(f"dept chosen: {dept}")

    kb = [
        [InlineKeyboardButton("🏭 Запит на склад", callback_data="type_Склад")],
        [InlineKeyboardButton("📅 Актуалізація дат", callback_data="type_Дати")],
        [InlineKeyboardButton("💰 Компенсація", callback_data="type_Компенсація")],
        [InlineKeyboardButton("🔧 Підряд", callback_data="type_Підряд")],
    ]
    await q.edit_message_text(
        f"Відділ: *{dept}*\n\nОберіть тип заявки:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return REQ_TYPE

async def step_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    req_type = q.data.replace("type_", "")
    context.user_data["request_type"] = req_type
    logger.info(f"type chosen: {req_type}")

    await q.edit_message_text(
        f"Тип: *{req_type}*\n\n"
        f"📋 Крок 1/4\n"
        f"Введіть *номер замовлення*\n_(наприклад: 838)_",
        parse_mode="Markdown"
    )
    return ORDER_NUM

async def step_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order_num"] = update.message.text.strip()
    await update.message.reply_text(
        "🪑 Крок 2/4\nВведіть *виріб*\n_(наприклад: Шафа, Кухня, Вітальня)_",
        parse_mode="Markdown"
    )
    return PRODUCT

async def step_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["product"] = update.message.text.strip()
    await update.message.reply_text(
        "📝 Крок 3/4\nВведіть *деталі запиту*\n_(артикул, кількість, сума)_",
        parse_mode="Markdown"
    )
    return DETAILS

async def step_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["details"] = update.message.text.strip()
    await update.message.reply_text(
        "📅 Крок 4/4\nВведіть *дедлайн*\n_(наприклад: 25.05.2026)_",
        parse_mode="Markdown"
    )
    return DEADLINE

async def step_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["deadline"] = update.message.text.strip()
    d = context.user_data

    text = (
        f"📋 *Перевірте заявку:*\n\n"
        f"🏢 Відділ: {d['department']}\n"
        f"📌 Тип: {d['request_type']}\n"
        f"🔢 Замовлення: #{d['order_num']}\n"
        f"🪑 Виріб: {d['product']}\n"
        f"📝 Деталі: {d['details']}\n"
        f"📅 Дедлайн: {d['deadline']}"
    )
    kb = [
        [InlineKeyboardButton("✅ Підтвердити", callback_data="confirm_yes")],
        [InlineKeyboardButton("🔄 Почати знову", callback_data="confirm_no")],
    ]
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
    return CONFIRM

async def step_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "confirm_no":
        await q.edit_message_text("Гаразд, починаємо знову. Натисніть /start")
        return ConversationHandler.END

    d = context.user_data
    request_id = next_id()
    created_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    chat_id    = q.from_user.id
    user_name  = q.from_user.full_name

    group_text = (
        f"🆕 *Нова заявка {request_id}*\n"
        f"👤 {user_name}\n\n"
        f"🏢 {d['department']} | 📌 {d['request_type']}\n"
        f"🔢 Замовлення: #{d['order_num']}\n"
        f"🪑 Виріб: {d['product']}\n"
        f"📝 {d['details']}\n"
        f"📅 Дедлайн: {d['deadline']}\n\n"
        f"_Reply на це повідомлення щоб відповісти майстру_"
    )

    try:
        group_msg = await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=group_text,
            parse_mode="Markdown",
            message_thread_id=GROUP_TOPIC_ID,
        )
        msg_id = group_msg.message_id
    except Exception as e:
        logger.error(f"Group send error: {e}")
        msg_id = 0

    save_to_sheet({
        "request_id": request_id, "created_at": created_at,
        "department": d["department"], "request_type": d["request_type"],
        "order_num": d["order_num"], "product": d["product"],
        "details": d["details"], "deadline": d["deadline"],
        "chat_id": chat_id, "message_id_group": msg_id,
    })

    if "msg_map" not in context.bot_data:
        context.bot_data["msg_map"] = {}
    context.bot_data["msg_map"][msg_id] = {
        "chat_id": chat_id, "request_id": request_id,
        "product": d["product"], "order_num": d["order_num"],
    }

    await q.edit_message_text(
        f"✅ *Заявку {request_id} подано!*\n\n"
        f"Менеджер отримав ваш запит.\n"
        f"Відповідь прийде сюди автоматично.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.reply_to_message:
        return
    if msg.chat.id != GROUP_CHAT_ID:
        return

    replied_id = msg.reply_to_message.message_id
    msg_map = context.bot_data.get("msg_map", {})
    if replied_id not in msg_map:
        return

    info = msg_map[replied_id]
    await context.bot.send_message(
        chat_id=info["chat_id"],
        text=(
            f"📬 *Відповідь на {info['request_id']}*\n\n"
            f"🪑 {info['product']} (#{info['order_num']})\n"
            f"👤 {msg.from_user.full_name}:\n\n"
            f"{msg.text}"
        ),
        parse_mode="Markdown"
    )
    await msg.reply_text("✅ Відповідь надіслано майстру")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Скасовано. Натисніть /start")
    return ConversationHandler.END

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            DEPT:      [CallbackQueryHandler(step_dept,    pattern="^dept_")],
            REQ_TYPE:  [CallbackQueryHandler(step_type,    pattern="^type_")],
            ORDER_NUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_order)],
            PRODUCT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, step_product)],
            DETAILS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, step_details)],
            DEADLINE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, step_deadline)],
            CONFIRM:   [CallbackQueryHandler(step_confirm, pattern="^confirm_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(conv)
    app.add_handler(MessageHandler(
        filters.Chat(GROUP_CHAT_ID) & filters.REPLY & filters.TEXT,
        handle_reply
    ))

    logger.info("Starting bot...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

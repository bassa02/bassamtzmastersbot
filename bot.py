import logging
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Конфіг (береться з env-змінних) ──────────────────────────────────────────
BOT_TOKEN        = os.environ["BOT_TOKEN"]
GROUP_CHAT_ID    = int(os.environ["GROUP_CHAT_ID"])    # ID групи БАССА МТЗ
GROUP_TOPIC_ID   = int(os.environ["GROUP_TOPIC_ID"])   # ID гілки "Запити від бригадирів"
SHEET_ID         = os.environ["SHEET_ID"]              # ID Google Таблиці
GOOGLE_CREDS     = os.environ["GOOGLE_CREDS"]          # JSON-рядок з credentials

# ── Стани розмови ─────────────────────────────────────────────────────────────
DEPT, REQ_TYPE, ORDER_NUM, PRODUCT, DETAILS, DEADLINE, CONFIRM = range(7)

DEPARTMENTS = ["Майстри", "Виробництво", "Комерційний"]

REQUEST_TYPES = {
    "Майстри":      ["Склад", "Дати", "Компенсація", "Підряд"],
    "Виробництво":  ["Склад", "Дати", "Компенсація", "Підряд"],
    "Комерційний":  ["Склад", "Дати", "Компенсація", "Підряд"],
}

# ── Google Sheets ─────────────────────────────────────────────────────────────
def get_sheet():
    import json
    creds_dict = json.loads(GOOGLE_CREDS)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SHEET_ID).sheet1
    return sheet

def append_to_sheet(data: dict):
    try:
        sheet = get_sheet()
        # Якщо таблиця порожня — додаємо заголовки
        if sheet.row_count < 1 or not sheet.row_values(1):
            headers = [
                "request_id", "created_at", "department", "request_type",
                "order_num", "product", "details", "deadline",
                "status", "manager_comment", "updated_at",
                "chat_id_master", "message_id_group"
            ]
            sheet.append_row(headers)
        sheet.append_row([
            data["request_id"],
            data["created_at"],
            data["department"],
            data["request_type"],
            data["order_num"],
            data["product"],
            data["details"],
            data["deadline"],
            "Нова",
            "",
            "",
            str(data["chat_id_master"]),
            str(data.get("message_id_group", "")),
        ])
    except Exception as e:
        logger.error(f"Sheets error: {e}")

def update_sheet_status(request_id: str, status: str, comment: str, message_id_group: str):
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        for i, row in enumerate(records, start=2):
            if str(row.get("request_id")) == str(request_id):
                col_status  = sheet.row_values(1).index("status") + 1
                col_comment = sheet.row_values(1).index("manager_comment") + 1
                col_updated = sheet.row_values(1).index("updated_at") + 1
                sheet.update_cell(i, col_status,  status)
                sheet.update_cell(i, col_comment, comment)
                sheet.update_cell(i, col_updated, datetime.now().strftime("%d.%m.%Y %H:%M"))
                break
    except Exception as e:
        logger.error(f"Sheets update error: {e}")

# ── Лічильник ID ──────────────────────────────────────────────────────────────
_counter_file = "counter.txt"

def next_request_id() -> str:
    try:
        with open(_counter_file, "r") as f:
            n = int(f.read().strip()) + 1
    except Exception:
        n = 1
    with open(_counter_file, "w") as f:
        f.write(str(n))
    return f"REQ-{n:04d}"

# ── /start ─────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    keyboard = [[InlineKeyboardButton(d, callback_data=f"dept:{d}")] for d in DEPARTMENTS]
    await update.message.reply_text(
        "👋 Вітаю!\n\nОберіть ваш відділ:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return DEPT

# ── Крок 1: відділ ────────────────────────────────────────────────────────────
async def choose_dept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    dept = query.data.split(":")[1]
    context.user_data["department"] = dept

    types = REQUEST_TYPES[dept]
    keyboard = [[InlineKeyboardButton(t, callback_data=f"type:{t}")] for t in types]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back:start")])
    await query.edit_message_text(
        f"Відділ: *{dept}*\n\nОберіть тип заявки:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return REQ_TYPE

# ── Крок 2: тип заявки ────────────────────────────────────────────────────────
async def choose_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    req_type = query.data.split(":")[1]
    context.user_data["request_type"] = req_type

    await query.edit_message_text(
        f"Тип: *{req_type}*\n\n📋 Введіть *номер замовлення*\n_(наприклад: 838)_",
        parse_mode="Markdown"
    )
    return ORDER_NUM

# ── Крок 3: номер замовлення ──────────────────────────────────────────────────
async def get_order_num(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["order_num"] = update.message.text.strip()
    await update.message.reply_text(
        "🪑 Введіть *виріб*\n_(наприклад: Шафа, Кухня, Вітальня)_",
        parse_mode="Markdown"
    )
    return PRODUCT

# ── Крок 4: виріб ─────────────────────────────────────────────────────────────
async def get_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["product"] = update.message.text.strip()
    await update.message.reply_text(
        "📝 Введіть *деталі запиту*\n_(артикул, кількість, сума — все що потрібно)_",
        parse_mode="Markdown"
    )
    return DETAILS

# ── Крок 5: деталі ────────────────────────────────────────────────────────────
async def get_details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["details"] = update.message.text.strip()
    await update.message.reply_text(
        "📅 Введіть *дедлайн*\n_(наприклад: 25.05.2026 або «до кінця тижня»)_",
        parse_mode="Markdown"
    )
    return DEADLINE

# ── Крок 6: дедлайн → підтвердження ──────────────────────────────────────────
async def get_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["deadline"] = update.message.text.strip()
    d = context.user_data

    summary = (
        f"📋 *Перевірте заявку:*\n\n"
        f"🏢 Відділ: {d['department']}\n"
        f"📌 Тип: {d['request_type']}\n"
        f"🔢 Замовлення: {d['order_num']}\n"
        f"🪑 Виріб: {d['product']}\n"
        f"📝 Деталі: {d['details']}\n"
        f"📅 Дедлайн: {d['deadline']}"
    )
    keyboard = [
        [InlineKeyboardButton("✅ Підтвердити", callback_data="confirm:yes")],
        [InlineKeyboardButton("🔄 Почати знову", callback_data="confirm:restart")],
    ]
    await update.message.reply_text(
        summary,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CONFIRM

# ── Крок 7: підтвердження → запис ────────────────────────────────────────────
async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action = query.data.split(":")[1]

    if action == "restart":
        await query.edit_message_text("Гаразд, починаємо знову. Натисніть /start")
        return ConversationHandler.END

    d = context.user_data
    request_id  = next_request_id()
    created_at  = datetime.now().strftime("%d.%m.%Y %H:%M")
    chat_id     = query.from_user.id
    user_name   = query.from_user.full_name

    # Повідомлення для групи
    group_text = (
        f"🆕 *Нова заявка {request_id}*\n"
        f"👤 Від: {user_name}\n\n"
        f"🏢 Відділ: {d['department']}\n"
        f"📌 Тип: {d['request_type']}\n"
        f"🔢 Замовлення: #{d['order_num']}\n"
        f"🪑 Виріб: {d['product']}\n"
        f"📝 Деталі: {d['details']}\n"
        f"📅 Дедлайн: {d['deadline']}\n\n"
        f"💬 _Щоб відповісти — зробіть reply на це повідомлення_"
    )

    # Надсилаємо в групу у гілку
    group_msg = await context.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=group_text,
        parse_mode="Markdown",
        message_thread_id=GROUP_TOPIC_ID,
    )

    # Зберігаємо в Sheets
    append_to_sheet({
        "request_id":      request_id,
        "created_at":      created_at,
        "department":      d["department"],
        "request_type":    d["request_type"],
        "order_num":       d["order_num"],
        "product":         d["product"],
        "details":         d["details"],
        "deadline":        d["deadline"],
        "chat_id_master":  chat_id,
        "message_id_group": group_msg.message_id,
    })

    # Зберігаємо маппінг: message_id_group → chat_id_master
    # щоб потім знайти куди відповідати
    if "msg_map" not in context.bot_data:
        context.bot_data["msg_map"] = {}
    context.bot_data["msg_map"][group_msg.message_id] = {
        "chat_id": chat_id,
        "request_id": request_id,
        "product": d["product"],
        "order_num": d["order_num"],
    }

    await query.edit_message_text(
        f"✅ *Заявку {request_id} подано!*\n\n"
        f"Ваш запит передано менеджеру.\n"
        f"Як тільки буде відповідь — ви отримаєте сповіщення тут.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ── Відповідь менеджера з групи ───────────────────────────────────────────────
async def handle_group_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    # Перевіряємо: це reply у правильній групі та гілці
    if not msg or not msg.reply_to_message:
        return
    if msg.chat.id != GROUP_CHAT_ID:
        return
    if msg.message_thread_id != GROUP_TOPIC_ID:
        return

    replied_msg_id = msg.reply_to_message.message_id
    msg_map = context.bot_data.get("msg_map", {})

    if replied_msg_id not in msg_map:
        return  # Відповідь не на заявку

    info       = msg_map[replied_msg_id]
    master_id  = info["chat_id"]
    request_id = info["request_id"]
    manager    = msg.from_user.full_name
    answer     = msg.text

    # Надсилаємо майстру
    await context.bot.send_message(
        chat_id=master_id,
        text=(
            f"📬 *Відповідь на заявку {request_id}*\n\n"
            f"🪑 Виріб: {info['product']} (#{info['order_num']})\n"
            f"👤 Менеджер: {manager}\n\n"
            f"💬 {answer}"
        ),
        parse_mode="Markdown"
    )

    # Оновлюємо Sheets
    update_sheet_status(request_id, "Відповідь надана", answer, str(replied_msg_id))

    # Підтверджуємо менеджеру
    await msg.reply_text("✅ Відповідь надіслано майстру")

# ── Скасування ────────────────────────────────────────────────────────────────
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Скасовано. Натисніть /start щоб почати знову.")
    return ConversationHandler.END

# ── Помилка ───────────────────────────────────────────────────────────────────
async def error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            DEPT:      [CallbackQueryHandler(choose_dept,  pattern="^dept:")],
            REQ_TYPE:  [CallbackQueryHandler(choose_type,  pattern="^type:")],
            ORDER_NUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_order_num)],
            PRODUCT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_product)],
            DETAILS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_details)],
            DEADLINE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, get_deadline)],
            CONFIRM:   [CallbackQueryHandler(confirm, pattern="^confirm:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv)

    # Хендлер для відповідей менеджера з групи
    app.add_handler(MessageHandler(
        filters.Chat(GROUP_CHAT_ID) & filters.REPLY & filters.TEXT,
        handle_group_reply
    ))

    app.add_error_handler(error_handler)

    logger.info("Bot started...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

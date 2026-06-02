import logging
import os
import json
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler,
    JobQueue
)
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN      = os.environ["BOT_TOKEN"]
GROUP_CHAT_ID  = int(os.environ["GROUP_CHAT_ID"])
GROUP_TOPIC_ID = int(os.environ["GROUP_TOPIC_ID"])
SHEET_ID       = os.environ["SHEET_ID"]
GOOGLE_CREDS   = os.environ["GOOGLE_CREDS"]

# Адмін для дайджесту
ADMIN_USERNAME = "vbm02"
ADMIN_CHAT_ID  = None  # заповниться при першому /start адміна

# Логісти username → chat_id (заповнюється автоматично)
LOGIST_USERNAMES = ["TsapiukM", "Yuliia_lohanets", "Ievgenanosov", "B_DH_1"]
LOGIST_CHAT_IDS  = {}  # username → chat_id

# Таймери нагадувань (секунди)
REMINDER_TIMES = {
    "Дати":        30 * 60,       # 30 хвилин
    "Підряд":      60 * 60,       # 1 година
    "Склад":       12 * 60 * 60,  # 12 годин
    "Компенсація": 12 * 60 * 60,  # 12 годин
}
AUTO_CLOSE_TIME = 24 * 60 * 60  # 24 години

# ── Стани ────────────────────────────────────────────────────────────────────
(DEPT, REQ_TYPE, SUB_TYPE, PRIORITY,
 ORDER_NUM, PRODUCT, DETAILS, DEADLINE, PHOTO,
 CONFIRM, REJECT_COMMENT) = range(11)

# ── Структура ─────────────────────────────────────────────────────────────────
STRUCTURE = {
    "Склад": {
        "🔩 Фурнітура":           "@TsapiukM",
        "🔧 Метизи":              "@Yuliia_lohanets",
        "🪟 Скло / Дзеркало":     "@Ievgenanosov",
        "📏 Кромка / Профіль":    "@Yuliia_lohanets",
        "🏗 Метал":               "@Yuliia_lohanets",
    },
    "Підряд": {
        "🧴 Шкіра / Тканина":      "@B_DH_1",
        "🎨 Порошкове фарбування": "@Ievgenanosov",
        "🪵 Шпонування":           "@TsapiukM",
        "🪨 Камінь":               "@B_DH_1",
        "🗜 Прес / Склеювання":    "@TsapiukM",
    },
    "Дати": {
        "📦 Матеріал": None,
        "🔨 Підряд":   None,
    },
    "Компенсація": {},
}

DATES_TAG = "@TsapiukM @Ievgenanosov @Yuliia_lohanets @B_DH_1"
COMP_TAG  = "@B_DH_1"

REPLY_HINT = {
    "Склад":       "📋 _Вкажіть у відповіді: №видаткової накладної_",
    "Підряд":      "📋 _Вкажіть у відповіді: дату орієнтовної готовності_",
    "Дати":        "📋 _Вкажіть у відповіді: актуальну дату_",
    "Компенсація": "📋 _Вкажіть у відповіді: підтвердження або коментар_",
}

# ── Google Sheets ─────────────────────────────────────────────────────────────
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
                "request_id","created_at","department","sub_type","priority",
                "order_num","product","details","deadline",
                "status","manager_comment","updated_at",
                "chat_id_master","message_id_group","logist_tag"
            ])
        sheet.append_row([
            data["request_id"], data["created_at"], data["department"],
            data.get("sub_type",""), data.get("priority","Звичайний"),
            data["order_num"], data["product"],
            data["details"], data.get("deadline",""),
            "Нова", "", "",
            str(data["chat_id"]), str(data.get("message_id_group","")),
            data.get("tag","")
        ])
    except Exception as e:
        logger.error(f"Sheet error: {e}")

def update_sheet_status(request_id, status, comment=""):
    try:
        sheet = get_sheet()
        headers = sheet.row_values(1)
        records = sheet.get_all_records()
        for i, row in enumerate(records, start=2):
            if str(row.get("request_id")) == str(request_id):
                sheet.update_cell(i, headers.index("status")+1, status)
                sheet.update_cell(i, headers.index("manager_comment")+1, comment)
                sheet.update_cell(i, headers.index("updated_at")+1,
                                  datetime.now().strftime("%d.%m.%Y %H:%M"))
                break
    except Exception as e:
        logger.error(f"Sheet update error: {e}")

def get_open_requests(logist_tag=None):
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        result = []
        for row in records:
            if row.get("status") in ("Нова", "В роботі", "Повернено"):
                if logist_tag is None or logist_tag in str(row.get("logist_tag","")):
                    result.append(row)
        return result
    except Exception as e:
        logger.error(f"Sheet read error: {e}")
        return []

def get_user_requests(chat_id):
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        return [r for r in records if str(r.get("chat_id_master")) == str(chat_id)]
    except Exception as e:
        logger.error(f"Sheet read error: {e}")
        return []

# ── ID лічильник ──────────────────────────────────────────────────────────────
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

# ── /start ────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user

    # Зберігаємо chat_id логістів і адміна
    uname = user.username or ""
    if uname == ADMIN_USERNAME:
        global ADMIN_CHAT_ID
        ADMIN_CHAT_ID = user.id
    if uname in LOGIST_USERNAMES:
        LOGIST_CHAT_IDS[uname] = user.id

    kb = [
        [InlineKeyboardButton("🏭 Склад",        callback_data="dept_Склад")],
        [InlineKeyboardButton("🔨 Підряд",        callback_data="dept_Підряд")],
        [InlineKeyboardButton("📅 Дати",          callback_data="dept_Дати")],
        [InlineKeyboardButton("💰 Компенсація",   callback_data="dept_Компенсація")],
    ]
    await update.message.reply_text(
        "👋 Вітаю!\n\nОберіть тип запиту:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return DEPT

# ── Крок 1: тип ───────────────────────────────────────────────────────────────
async def step_dept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    dept = q.data.replace("dept_", "")
    context.user_data["department"] = dept
    subtypes = STRUCTURE.get(dept, {})

    if not subtypes:
        context.user_data["sub_type"] = ""
        return await ask_priority(q, context)

    kb = [[InlineKeyboardButton(name, callback_data=f"sub_{name}")] for name in subtypes]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_start")])
    await q.edit_message_text(
        f"Тип: *{dept}*\n\nОберіть підкатегорію:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return SUB_TYPE

# ── Крок 2: підтип ────────────────────────────────────────────────────────────
async def step_subtype(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "back_start":
        context.user_data.clear()
        kb = [
            [InlineKeyboardButton("🏭 Склад",        callback_data="dept_Склад")],
            [InlineKeyboardButton("🔨 Підряд",        callback_data="dept_Підряд")],
            [InlineKeyboardButton("📅 Дати",          callback_data="dept_Дати")],
            [InlineKeyboardButton("💰 Компенсація",   callback_data="dept_Компенсація")],
        ]
        await q.edit_message_text("Оберіть тип запиту:", reply_markup=InlineKeyboardMarkup(kb))
        return DEPT

    sub = q.data.replace("sub_", "")
    context.user_data["sub_type"] = sub
    return await ask_priority(q, context)

async def ask_priority(q, context):
    dept = context.user_data["department"]
    sub  = context.user_data.get("sub_type", "")
    label = f"{dept}" + (f" → {sub}" if sub else "")
    kb = [
        [InlineKeyboardButton("🔴 Терміново",   callback_data="pri_Терміново")],
        [InlineKeyboardButton("🟢 Звичайний",   callback_data="pri_Звичайний")],
    ]
    await q.edit_message_text(
        f"*{label}*\n\nОберіть пріоритет:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return PRIORITY

# ── Крок 3: пріоритет ─────────────────────────────────────────────────────────
async def step_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["priority"] = q.data.replace("pri_", "")
    await q.edit_message_text(
        f"📋 Крок 1/4\nВведіть *номер замовлення*\n_(наприклад: 838)_",
        parse_mode="Markdown"
    )
    return ORDER_NUM

# ── Кроки форми ───────────────────────────────────────────────────────────────
async def step_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order_num"] = update.message.text.strip()
    await update.message.reply_text(
        "🪑 Крок 2/4\nВведіть *виріб*\n_(Шафа, Кухня, Вітальня, тощо)_",
        parse_mode="Markdown"
    )
    return PRODUCT

async def step_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["product"] = update.message.text.strip()
    dept = context.user_data["department"]
    if dept == "Компенсація":
        prompt = "📝 Крок 3/4\nВведіть *деталі*\n_(За що оплата та сума, наприклад: Шурупи — 120 грн)_"
    elif dept == "Дати":
        prompt = "📝 Крок 3/4\nВведіть *що саме уточнити*"
    else:
        prompt = "📝 Крок 3/4\nВведіть *деталі запиту*\n_(артикул, кількість, опис)_"
    await update.message.reply_text(prompt, parse_mode="Markdown")
    return DETAILS

async def step_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["details"] = update.message.text.strip()
    dept = context.user_data["department"]
    if dept == "Компенсація":
        await update.message.reply_text(
            "📎 Крок 4/4\nДодайте *фото чеку або квитанції*\n_(або напишіть «пропустити»)_",
            parse_mode="Markdown"
        )
        return PHOTO
    await update.message.reply_text(
        "📅 Крок 4/4\nВведіть *дедлайн*\n_(наприклад: 05.06.2026)_",
        parse_mode="Markdown"
    )
    return DEADLINE

async def step_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        context.user_data["photo_id"] = update.message.photo[-1].file_id
    elif update.message.document:
        context.user_data["photo_id"] = update.message.document.file_id
        context.user_data["photo_is_doc"] = True
    else:
        context.user_data["photo_id"] = None
    return await show_confirm(update.message, context)

async def step_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["deadline"] = update.message.text.strip()
    return await show_confirm(update.message, context)

async def show_confirm(msg: Message, context: ContextTypes.DEFAULT_TYPE):
    d    = context.user_data
    dept = d["department"]
    sub  = d.get("sub_type", "")
    pri  = d.get("priority", "Звичайний")
    pri_icon = "🔴" if pri == "Терміново" else "🟢"

    lines = [f"📋 *Перевірте заявку:*\n"]
    lines.append(f"{pri_icon} Пріоритет: {pri}")
    lines.append(f"📌 Тип: {dept}" + (f" → {sub}" if sub else ""))
    lines.append(f"🔢 Замовлення: #{d['order_num']}")
    lines.append(f"🪑 Виріб: {d['product']}")
    lines.append(f"📝 Деталі: {d['details']}")
    if d.get("deadline"):
        lines.append(f"📅 Дедлайн: {d['deadline']}")
    if d.get("photo_id"):
        lines.append(f"📎 Фото: додано")

    kb = [
        [InlineKeyboardButton("✅ Підтвердити",  callback_data="confirm_yes")],
        [InlineKeyboardButton("🔄 Почати знову", callback_data="confirm_no")],
    ]
    await msg.reply_text(
        "\n".join(lines), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return CONFIRM

# ── Підтвердження → відправка ─────────────────────────────────────────────────
async def step_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "confirm_no":
        await q.edit_message_text("Гаразд, починаємо знову. Натисніть /start")
        return ConversationHandler.END

    d          = context.user_data
    dept       = d["department"]
    sub        = d.get("sub_type", "")
    pri        = d.get("priority", "Звичайний")
    pri_icon   = "🔴" if pri == "Терміново" else "🟢"
    request_id = next_id()
    created_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    chat_id    = q.from_user.id
    user_name  = q.from_user.full_name

    if dept == "Компенсація":
        tag = COMP_TAG
    elif dept == "Дати":
        tag = DATES_TAG
    else:
        tag = STRUCTURE.get(dept, {}).get(sub, "")

    hint = REPLY_HINT.get(dept, "")

    group_text = (
        f"{pri_icon} *{pri} | Заявка {request_id}*\n"
        f"👤 {user_name}\n\n"
        f"📌 {dept}" + (f" → {sub}" if sub else "") + "\n"
        f"🔢 Замовлення: #{d['order_num']}\n"
        f"🪑 Виріб: {d['product']}\n"
        f"📝 {d['details']}\n"
    )
    if d.get("deadline"):
        group_text += f"📅 Дедлайн: {d['deadline']}\n"
    group_text += f"\n{tag}\n{hint}"

    try:
        if d.get("photo_id"):
            if d.get("photo_is_doc"):
                group_msg = await context.bot.send_document(
                    chat_id=GROUP_CHAT_ID, document=d["photo_id"],
                    caption=group_text, parse_mode="Markdown",
                    message_thread_id=GROUP_TOPIC_ID,
                )
            else:
                group_msg = await context.bot.send_photo(
                    chat_id=GROUP_CHAT_ID, photo=d["photo_id"],
                    caption=group_text, parse_mode="Markdown",
                    message_thread_id=GROUP_TOPIC_ID,
                )
        else:
            group_msg = await context.bot.send_message(
                chat_id=GROUP_CHAT_ID, text=group_text,
                parse_mode="Markdown", message_thread_id=GROUP_TOPIC_ID,
            )
        msg_id = group_msg.message_id
    except Exception as e:
        logger.error(f"Group send error: {e}")
        msg_id = 0

    save_to_sheet({
        "request_id": request_id, "created_at": created_at,
        "department": dept, "sub_type": sub, "priority": pri,
        "order_num": d["order_num"], "product": d["product"],
        "details": d["details"], "deadline": d.get("deadline",""),
        "chat_id": chat_id, "message_id_group": msg_id, "tag": tag,
    })

    # Зберігаємо в пам'яті
    if "msg_map" not in context.bot_data:
        context.bot_data["msg_map"] = {}
    context.bot_data["msg_map"][msg_id] = {
        "chat_id": chat_id, "request_id": request_id,
        "product": d["product"], "order_num": d["order_num"],
        "dept": dept, "group_msg_id": msg_id,
    }

    # Таймер нагадування логісту
    reminder_sec = REMINDER_TIMES.get(dept, 12 * 60 * 60)
    context.job_queue.run_once(
        remind_logist,
        when=reminder_sec,
        data={"request_id": request_id, "msg_id": msg_id, "tag": tag, "dept": dept},
        name=f"remind_{request_id}"
    )

    # Таймер автозакриття через 24г після відповіді логіста
    # (запускається пізніше, при відповіді логіста)

    await q.edit_message_text(
        f"✅ *Заявку {request_id} подано!*\n\n"
        f"Логіст отримав сповіщення.\n"
        f"Відповідь прийде сюди автоматично.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ── Нагадування логісту ───────────────────────────────────────────────────────
async def remind_logist(context: ContextTypes.DEFAULT_TYPE):
    job   = context.job
    data  = job.data
    rid   = data["request_id"]
    tag   = data["tag"]
    dept  = data["dept"]

    # Перевіряємо чи заявка ще відкрита
    requests = get_open_requests()
    open_ids = [r.get("request_id") for r in requests]
    if rid not in open_ids:
        return  # вже закрита

    time_label = {
        "Дати": "30 хвилин", "Підряд": "1 годину",
        "Склад": "12 годин", "Компенсація": "12 годин"
    }.get(dept, "")

    try:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=(
                f"⚠️ *Нагадування!*\n\n"
                f"Заявка *{rid}* без відповіді вже {time_label}.\n\n"
                f"{tag} — будь ласка, дайте відповідь."
            ),
            parse_mode="Markdown",
            message_thread_id=GROUP_TOPIC_ID,
        )
    except Exception as e:
        logger.error(f"Reminder error: {e}")

# ── Відповідь логіста з групи ─────────────────────────────────────────────────
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

    info       = msg_map[replied_id]
    master_id  = info["chat_id"]
    request_id = info["request_id"]
    logist     = msg.from_user.full_name

    # Оновлюємо статус
    update_sheet_status(request_id, "В роботі", msg.text or "")

    # Скасовуємо нагадування
    jobs = context.job_queue.get_jobs_by_name(f"remind_{request_id}")
    for job in jobs:
        job.schedule_removal()

    # Надсилаємо майстру з кнопками підтвердження
    kb = [
        [InlineKeyboardButton("✅ Виконано, дякую!", callback_data=f"done_{request_id}_{replied_id}")],
        [InlineKeyboardButton("❌ Не вирішено",      callback_data=f"reject_{request_id}_{replied_id}")],
    ]
    await context.bot.send_message(
        chat_id=master_id,
        text=(
            f"📬 *Відповідь на заявку {request_id}*\n\n"
            f"🪑 {info['product']} (#{info['order_num']})\n"
            f"👤 {logist}:\n\n"
            f"{msg.text or '(медіа)'}\n\n"
            f"_Підтвердіть виконання або поверніть заявку:_"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb)
    )

    # Таймер автозакриття через 24г
    context.job_queue.run_once(
        auto_close,
        when=AUTO_CLOSE_TIME,
        data={"request_id": request_id, "group_msg_id": info.get("group_msg_id"), "master_id": master_id},
        name=f"autoclose_{request_id}"
    )

# ── Підтвердження виконання бригадиром ───────────────────────────────────────
async def handle_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts      = q.data.split("_")
    request_id = parts[1]
    msg_id     = int(parts[2])

    update_sheet_status(request_id, "Виконано")

    # Видаляємо з групи
    try:
        await context.bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=msg_id)
    except Exception as e:
        logger.error(f"Delete error: {e}")

    # Скасовуємо автозакриття
    jobs = context.job_queue.get_jobs_by_name(f"autoclose_{request_id}")
    for job in jobs:
        job.schedule_removal()

    await q.edit_message_text(
        f"✅ *Заявку {request_id} закрито!*\n\nДякуємо за підтвердження.",
        parse_mode="Markdown"
    )

# ── Відхилення бригадиром ─────────────────────────────────────────────────────
async def handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts      = q.data.split("_")
    request_id = parts[1]
    msg_id     = int(parts[2])

    context.user_data["rejecting_request_id"] = request_id
    context.user_data["rejecting_msg_id"]      = msg_id

    await q.edit_message_text(
        f"📝 Напишіть *що саме не вирішено* по заявці {request_id}:\n"
        f"_(Логіст отримає ваш коментар)_",
        parse_mode="Markdown"
    )
    return REJECT_COMMENT

async def step_reject_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment    = update.message.text.strip()
    request_id = context.user_data.get("rejecting_request_id")
    msg_id     = context.user_data.get("rejecting_msg_id")

    update_sheet_status(request_id, "Повернено", comment)

    # Скасовуємо автозакриття
    jobs = context.job_queue.get_jobs_by_name(f"autoclose_{request_id}")
    for job in jobs:
        job.schedule_removal()

    # Сповіщення в групу
    msg_map = context.bot_data.get("msg_map", {})
    info    = msg_map.get(msg_id, {})
    tag     = ""
    for v in STRUCTURE.values():
        for sub_tag in v.values():
            if sub_tag:
                tag = sub_tag
                break

    try:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=(
                f"🔄 *Заявку {request_id} повернено*\n\n"
                f"💬 Коментар бригадира: {comment}\n\n"
                f"Будь ласка, вирішіть питання."
            ),
            parse_mode="Markdown",
            message_thread_id=GROUP_TOPIC_ID,
        )
    except Exception as e:
        logger.error(f"Reject notify error: {e}")

    await update.message.reply_text(
        f"↩️ *Заявку {request_id} повернено логісту*\n\nВаш коментар передано.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ── Автозакриття через 24г ────────────────────────────────────────────────────
async def auto_close(context: ContextTypes.DEFAULT_TYPE):
    data       = context.job.data
    request_id = data["request_id"]
    msg_id     = data.get("group_msg_id")
    master_id  = data.get("master_id")

    requests = get_open_requests()
    open_ids = [r.get("request_id") for r in requests]
    if request_id not in open_ids:
        return

    update_sheet_status(request_id, "Виконано (авто)", "Автозакриття через 24г")

    try:
        if msg_id:
            await context.bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=msg_id)
    except:
        pass

    try:
        if master_id:
            await context.bot.send_message(
                chat_id=master_id,
                text=f"🕐 Заявку *{request_id}* автоматично закрито (24г без реакції).",
                parse_mode="Markdown"
            )
    except:
        pass

# ── /mytasks — для бригадира ──────────────────────────────────────────────────
async def my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id  = update.effective_user.id
    requests = get_user_requests(chat_id)

    if not requests:
        await update.message.reply_text("📭 У вас немає заявок.")
        return

    open_r   = [r for r in requests if r.get("status") not in ("Виконано", "Виконано (авто)")]
    closed_r = [r for r in requests if r.get("status") in ("Виконано", "Виконано (авто)")]

    text = f"📋 *Ваші заявки:*\n\n"
    if open_r:
        text += "*Відкриті:*\n"
        for r in open_r[-10:]:
            status = r.get("status","")
            icon = {"Нова":"🆕","В роботі":"👀","Повернено":"↩️"}.get(status,"❓")
            text += f"{icon} *{r['request_id']}* — {r.get('department','')} #{r.get('order_num','')}\n"
            text += f"   {r.get('product','')} | {status}\n"
    if closed_r:
        text += f"\n*Виконані (останні 5):*\n"
        for r in closed_r[-5:]:
            text += f"✅ {r['request_id']} — {r.get('department','')} #{r.get('order_num','')}\n"

    await update.message.reply_text(text, parse_mode="Markdown")

# ── /myqueue — для логіста ────────────────────────────────────────────────────
async def my_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uname    = update.effective_user.username or ""
    tag      = f"@{uname}"
    requests = get_open_requests(logist_tag=tag)

    if not requests:
        await update.message.reply_text("📭 У вас немає відкритих заявок.")
        return

    text = f"📋 *Ваші відкриті заявки ({len(requests)}):*\n\n"
    for r in requests:
        status = r.get("status","")
        icon = {"Нова":"🆕","В роботі":"👀","Повернено":"↩️"}.get(status,"❓")
        text += f"{icon} *{r['request_id']}* — {r.get('department','')} #{r.get('order_num','')}\n"
        text += f"   {r.get('product','')} | {r.get('created_at','')}\n\n"

    await update.message.reply_text(text, parse_mode="Markdown")

# ── Щоденний дайджест ─────────────────────────────────────────────────────────
async def daily_digest(context: ContextTypes.DEFAULT_TYPE):
    open_requests = get_open_requests()
    if not open_requests:
        return

    # Адміну — загальне зведення
    if ADMIN_CHAT_ID:
        by_dept = {}
        for r in open_requests:
            dept = r.get("department","?")
            by_dept.setdefault(dept, []).append(r)

        text = f"📊 *Ранковий дайджест — {datetime.now().strftime('%d.%m.%Y')}*\n\n"
        text += f"Всього відкритих заявок: *{len(open_requests)}*\n\n"
        for dept, reqs in by_dept.items():
            text += f"*{dept}:* {len(reqs)} заявок\n"
            for r in reqs[:3]:
                text += f"  • {r['request_id']} — #{r.get('order_num','')} {r.get('product','')}\n"
            if len(reqs) > 3:
                text += f"  _...та ще {len(reqs)-3}_\n"
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Digest admin error: {e}")

    # Кожному логісту — його заявки
    for uname, cid in LOGIST_CHAT_IDS.items():
        tag  = f"@{uname}"
        mine = get_open_requests(logist_tag=tag)
        if not mine:
            continue
        text = f"📋 *Ваші відкриті заявки на {datetime.now().strftime('%d.%m')}:*\n\n"
        for r in mine:
            status = r.get("status","")
            icon = {"Нова":"🆕","В роботі":"👀","Повернено":"↩️"}.get(status,"❓")
            text += f"{icon} *{r['request_id']}* — {r.get('department','')} #{r.get('order_num','')}\n"
            text += f"   {r.get('product','')}\n\n"
        try:
            await context.bot.send_message(chat_id=cid, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Digest logist error: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Скасовано. Натисніть /start")
    return ConversationHandler.END

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Щоденний дайджест о 8:00
    app.job_queue.run_daily(
        daily_digest,
        time=datetime.strptime("08:00", "%H:%M").time(),
        name="daily_digest"
    )

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            DEPT:           [CallbackQueryHandler(step_dept,     pattern="^dept_")],
            SUB_TYPE:       [CallbackQueryHandler(step_subtype,  pattern="^(sub_|back_)")],
            PRIORITY:       [CallbackQueryHandler(step_priority, pattern="^pri_")],
            ORDER_NUM:      [MessageHandler(filters.TEXT & ~filters.COMMAND, step_order)],
            PRODUCT:        [MessageHandler(filters.TEXT & ~filters.COMMAND, step_product)],
            DETAILS:        [MessageHandler(filters.TEXT & ~filters.COMMAND, step_details)],
            PHOTO:          [MessageHandler(filters.PHOTO | filters.Document.ALL | (filters.TEXT & ~filters.COMMAND), step_photo)],
            DEADLINE:       [MessageHandler(filters.TEXT & ~filters.COMMAND, step_deadline)],
            CONFIRM:        [CallbackQueryHandler(step_confirm,  pattern="^confirm_")],
            REJECT_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, step_reject_comment)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(handle_done,   pattern="^done_"))
    app.add_handler(CallbackQueryHandler(handle_reject, pattern="^reject_"))
    app.add_handler(CommandHandler("mytasks",  my_tasks))
    app.add_handler(CommandHandler("myqueue",  my_queue))
    app.add_handler(MessageHandler(
        filters.Chat(GROUP_CHAT_ID) & filters.REPLY & filters.TEXT,
        handle_reply
    ))

    logger.info("Starting bot...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

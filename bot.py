import logging
import os
import json
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler,
)
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN        = os.environ["BOT_TOKEN"]
GROUP_CHAT_ID    = int(os.environ["GROUP_CHAT_ID"])
GROUP_TOPIC_ID   = int(os.environ["GROUP_TOPIC_ID"])
GROUP_TOPIC_DONE = 12262
SHEET_ID         = os.environ["SHEET_ID"]
GOOGLE_CREDS     = os.environ["GOOGLE_CREDS"]

ADMIN_USERNAME = "vbm02"
ADMIN_CHAT_ID  = None
LOGIST_USERNAMES = ["TsapiukM", "Yuliia_lohanets", "Ievgenanosov", "B_DH_1"]
LOGIST_CHAT_IDS  = {}

REMINDER_TIMES = {
    "Дати": 30 * 60,
    "Підряд": 60 * 60,
    "Склад": 12 * 60 * 60,
    "Компенсація": 12 * 60 * 60,
}
AUTO_CLOSE_TIME = 24 * 60 * 60

(DEPT, SUB_TYPE, PRIORITY,
 ORDER_NUM, PRODUCT, DETAILS, DEADLINE, PHOTO,
 CONFIRM, REJECT_COMMENT) = range(10)

STRUCTURE = {
    "Склад": {
        "Фурнітура":        "@TsapiukM",
        "Метизи":           "@Yuliia_lohanets",
        "Скло / Дзеркало":  "@Ievgenanosov",
        "Кромка / Профіль": "@Yuliia_lohanets",
        "Метал":            "@Yuliia_lohanets",
    },
    "Підряд": {
        "Шкіра / Тканина":      "@B_DH_1",
        "Порошкове фарбування": "@Ievgenanosov",
        "Шпонування":           "@TsapiukM",
        "Камінь":               "@B_DH_1",
        "Прес / Склеювання":    "@TsapiukM",
        "Метал (обробка)":      "@Ievgenanosov",
        "Дерево / Масив":       "@Ievgenanosov",
    },
    "Дати": {
        "Матеріал":      None,
        "Послуги/Підряд": None,
    },
    "Компенсація": {},
}

DATES_SUB = {
    "Матеріал": {
        "Фурнітура":        "@TsapiukM",
        "Метизи":           "@Yuliia_lohanets",
        "Скло / Дзеркало":  "@Ievgenanosov",
        "Кромка / Профіль": "@Yuliia_lohanets",
        "Метал":            "@Yuliia_lohanets",
    },
    "Послуги/Підряд": {
        "Шкіра / Тканина":      "@B_DH_1",
        "Порошкове фарбування": "@Ievgenanosov",
        "Шпонування":           "@TsapiukM",
        "Камінь":               "@B_DH_1",
        "Прес / Склеювання":    "@TsapiukM",
        "Метал (обробка)":      "@Ievgenanosov",
        "Дерево / Масив":       "@Ievgenanosov",
    },
}

DATES_TAG = "@TsapiukM @Ievgenanosov @Yuliia_lohanets @B_DH_1"
COMP_TAG  = "@B_DH_1"

REPLY_HINT = {
    "Склад":       "Вкажіть у відповіді: N видаткової накладної",
    "Підряд":      "Вкажіть у відповіді: дату орієнтовної готовності",
    "Дати":        "Вкажіть у відповіді: актуальну дату",
    "Компенсація": "Вкажіть у відповіді: підтвердження або коментар",
}

# Google Sheets
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
                "chat_id_master","message_id_group","logist_tag",
                "deadline_response","responded_at","response_time_min","is_overdue"
            ])
        dept = data.get("department", "")
        limit_min = {"Дати":30,"Підряд":60,"Склад":720,"Компенсація":720}.get(dept, 720)
        deadline_resp = (datetime.now() + timedelta(minutes=limit_min)).strftime("%d.%m.%Y %H:%M")
        sheet.append_row([
            data["request_id"], data["created_at"], data["department"],
            data.get("sub_type",""), data.get("priority","Звичайний"),
            data["order_num"], data["product"],
            data["details"], data.get("deadline",""),
            "Нова", "", "",
            str(data["chat_id"]), str(data.get("message_id_group","")),
            data.get("tag",""), deadline_resp, "", "", ""
        ])
    except Exception as e:
        logger.error(f"Sheet error: {e}")

def update_sheet_status(request_id, status, comment="", record_response=False):
    try:
        sheet = get_sheet()
        headers = sheet.row_values(1)
        records = sheet.get_all_records()
        for i, row in enumerate(records, start=2):
            if str(row.get("request_id")) == str(request_id):
                now = datetime.now()
                now_str = now.strftime("%d.%m.%Y %H:%M")
                sheet.update_cell(i, headers.index("status")+1, status)
                sheet.update_cell(i, headers.index("manager_comment")+1, comment)
                sheet.update_cell(i, headers.index("updated_at")+1, now_str)
                if record_response and "responded_at" in headers:
                    sheet.update_cell(i, headers.index("responded_at")+1, now_str)
                    try:
                        created = datetime.strptime(str(row.get("created_at","")), "%d.%m.%Y %H:%M")
                        diff_min = int((now - created).total_seconds() / 60)
                        sheet.update_cell(i, headers.index("response_time_min")+1, diff_min)
                    except:
                        pass
                    try:
                        deadline_dt = datetime.strptime(str(row.get("deadline_response","")), "%d.%m.%Y %H:%M")
                        sheet.update_cell(i, headers.index("is_overdue")+1, "Так" if now > deadline_dt else "Ні")
                    except:
                        pass
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
        return []

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
    user = update.effective_user
    uname = user.username or ""
    if uname == ADMIN_USERNAME:
        global ADMIN_CHAT_ID
        ADMIN_CHAT_ID = user.id
    if uname in LOGIST_USERNAMES:
        LOGIST_CHAT_IDS[uname] = user.id

    kb = [
        [InlineKeyboardButton("Склад — отримати матеріали", callback_data="dept_Склад")],
        [InlineKeyboardButton("Підряд — передати в роботу", callback_data="dept_Підряд")],
        [InlineKeyboardButton("Дати — уточнити терміни",    callback_data="dept_Дати")],
        [InlineKeyboardButton("Компенсація — відшкодування", callback_data="dept_Компенсація")],
    ]
    await update.message.reply_text(
        "Вітаю! Оберіть тип запиту:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return DEPT

async def step_dept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    dept = q.data.replace("dept_", "")
    context.user_data["department"] = dept
    subtypes = STRUCTURE.get(dept, {})

    if not subtypes:
        context.user_data["sub_type"] = ""
        return await ask_priority(q, context)

    kb = [[InlineKeyboardButton(name, callback_data="sub_" + name)] for name in subtypes]
    kb.append([InlineKeyboardButton("Назад", callback_data="back_start")])
    await q.edit_message_text(
        "Тип: " + dept + "\n\nОберіть підкатегорію:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return SUB_TYPE

async def step_subtype(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "back_start":
        context.user_data.clear()
        kb = [
            [InlineKeyboardButton("Склад — отримати матеріали", callback_data="dept_Склад")],
            [InlineKeyboardButton("Підряд — передати в роботу", callback_data="dept_Підряд")],
            [InlineKeyboardButton("Дати — уточнити терміни",    callback_data="dept_Дати")],
            [InlineKeyboardButton("Компенсація — відшкодування", callback_data="dept_Компенсація")],
        ]
        await q.edit_message_text("Оберіть тип запиту:", reply_markup=InlineKeyboardMarkup(kb))
        return DEPT

    sub = q.data.replace("sub_", "")
    dept = context.user_data.get("department", "")

    if dept == "Дати" and sub in DATES_SUB:
        context.user_data["dates_category"] = sub
        subsubs = DATES_SUB[sub]
        kb = [[InlineKeyboardButton(name, callback_data="datesub_" + name)] for name in subsubs]
        kb.append([InlineKeyboardButton("Назад", callback_data="back_start")])
        await q.edit_message_text(
            "Дати -> " + sub + "\n\nОберіть підкатегорію:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return SUB_TYPE

    context.user_data["sub_type"] = sub
    return await ask_priority(q, context)

async def step_datesub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    datesub = q.data.replace("datesub_", "")
    dates_cat = context.user_data.get("dates_category", "")
    tag = DATES_SUB.get(dates_cat, {}).get(datesub, DATES_TAG)
    context.user_data["sub_type"] = dates_cat + " -> " + datesub
    context.user_data["dates_tag"] = tag
    return await ask_priority(q, context)

async def ask_priority(q, context):
    dept = context.user_data["department"]
    sub  = context.user_data.get("sub_type", "")
    label = dept + (" -> " + sub if sub else "")
    kb = [
        [InlineKeyboardButton("ТЕРМІНОВО", callback_data="pri_Терміново")],
        [InlineKeyboardButton("Звичайний", callback_data="pri_Звичайний")],
    ]
    await q.edit_message_text(
        label + "\n\nОберіть пріоритет:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return PRIORITY

async def step_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["priority"] = q.data.replace("pri_", "")
    await q.edit_message_text("Крок 1/4\nВведіть номер замовлення (наприклад: 838):")
    return ORDER_NUM

async def step_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["order_num"] = update.message.text.strip()
    await update.message.reply_text("Крок 2/4\nВведіть виріб (Шафа, Кухня, Вітальня):")
    return PRODUCT

async def step_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["product"] = update.message.text.strip()
    dept = context.user_data["department"]
    if dept == "Компенсація":
        await update.message.reply_text("Крок 3/4\nЗа що оплата та сума (наприклад: Шурупи — 120 грн):")
    elif dept == "Дати":
        await update.message.reply_text("Крок 3/4\nЩо саме уточнити:")
    else:
        await update.message.reply_text("Крок 3/4\nДеталі запиту (артикул, кількість, опис):")
    return DETAILS

async def step_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["details"] = update.message.text.strip()
    dept = context.user_data["department"]
    if dept == "Компенсація":
        await update.message.reply_text("Крок 4/4\nДодайте фото чеку або напишіть 'пропустити':")
        return PHOTO
    await update.message.reply_text("Крок 4/4\nВведіть дедлайн (наприклад: 05.06.2026):")
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

    text = "Перевірте заявку:\n\n"
    text += "Пріоритет: " + pri + "\n"
    text += "Тип: " + dept + (" -> " + sub if sub else "") + "\n"
    text += "Замовлення: #" + d["order_num"] + "\n"
    text += "Виріб: " + d["product"] + "\n"
    text += "Деталі: " + d["details"] + "\n"
    if d.get("deadline"):
        text += "Дедлайн: " + d["deadline"] + "\n"
    if d.get("photo_id"):
        text += "Фото: додано\n"

    kb = [
        [InlineKeyboardButton("Підтвердити", callback_data="confirm_yes")],
        [InlineKeyboardButton("Почати знову", callback_data="confirm_no")],
    ]
    await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return CONFIRM

async def step_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "confirm_no":
        await q.edit_message_text("Починаємо знову. Натисніть /start")
        return ConversationHandler.END

    d          = context.user_data
    dept       = d["department"]
    sub        = d.get("sub_type", "")
    pri        = d.get("priority", "Звичайний")
    request_id = next_id()
    created_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    chat_id    = q.from_user.id
    user_name  = q.from_user.full_name

    if dept == "Компенсація":
        tag = COMP_TAG
    elif dept == "Дати":
        tag = context.user_data.get("dates_tag", DATES_TAG)
    else:
        tag = STRUCTURE.get(dept, {}).get(sub, "")

    hint = REPLY_HINT.get(dept, "")

    group_text = pri + " | Заявка " + request_id + "\n"
    group_text += "Від: " + user_name + "\n\n"
    group_text += dept + (" -> " + sub if sub else "") + "\n"
    group_text += "Замовлення: #" + d["order_num"] + "\n"
    group_text += "Виріб: " + d["product"] + "\n"
    group_text += "Деталі: " + d["details"] + "\n"
    if d.get("deadline"):
        group_text += "Дедлайн: " + d["deadline"] + "\n"
    group_text += "\n" + tag + "\n" + hint

    try:
        if d.get("photo_id"):
            if d.get("photo_is_doc"):
                group_msg = await context.bot.send_document(
                    chat_id=GROUP_CHAT_ID, document=d["photo_id"],
                    caption=group_text, message_thread_id=GROUP_TOPIC_ID,
                )
            else:
                group_msg = await context.bot.send_photo(
                    chat_id=GROUP_CHAT_ID, photo=d["photo_id"],
                    caption=group_text, message_thread_id=GROUP_TOPIC_ID,
                )
        else:
            group_msg = await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=group_text,
                message_thread_id=GROUP_TOPIC_ID,
            )
        msg_id = group_msg.message_id
        logger.info(f"Sent to group: {request_id} msg_id={msg_id}")
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

    if "msg_map" not in context.bot_data:
        context.bot_data["msg_map"] = {}
    context.bot_data["msg_map"][msg_id] = {
        "chat_id": chat_id, "request_id": request_id,
        "product": d["product"], "order_num": d["order_num"],
        "dept": dept, "group_msg_id": msg_id, "tag": tag,
    }

    reminder_sec = REMINDER_TIMES.get(dept, 12 * 60 * 60)
    context.job_queue.run_once(
        remind_logist,
        when=reminder_sec,
        data={"request_id": request_id, "msg_id": msg_id, "tag": tag, "dept": dept},
        name="remind_" + request_id
    )

    await q.edit_message_text(
        "Заявку " + request_id + " подано!\n\nЛогіст отримав сповіщення. Відповідь прийде сюди автоматично."
    )
    return ConversationHandler.END

async def remind_logist(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    rid  = data["request_id"]
    tag  = data["tag"]
    dept = data["dept"]

    requests = get_open_requests()
    if rid not in [r.get("request_id") for r in requests]:
        return

    time_label = {"Дати":"30 хвилин","Підряд":"1 годину","Склад":"12 годин","Компенсація":"12 годин"}.get(dept,"")
    try:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="Нагадування! Заявка " + rid + " без відповіді вже " + time_label + ".\n" + tag,
            message_thread_id=GROUP_TOPIC_ID,
        )
    except Exception as e:
        logger.error(f"Reminder error: {e}")

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

    update_sheet_status(request_id, "В роботі", msg.text or "", record_response=True)

    now = datetime.now()
    try:
        sheet = get_sheet()
        records = sheet.get_all_records()
        created_str = ""
        for row in records:
            if str(row.get("request_id")) == str(request_id):
                created_str = str(row.get("created_at",""))
                break
        if created_str:
            created_dt = datetime.strptime(created_str, "%d.%m.%Y %H:%M")
            diff = int((now - created_dt).total_seconds() / 60)
            if diff < 60:
                response_time_str = str(diff) + " хв"
            else:
                response_time_str = str(diff // 60) + " год " + str(diff % 60) + " хв"
        else:
            response_time_str = "—"
    except:
        response_time_str = "—"

    context.bot_data["answer_" + request_id] = msg.text or "—"
    context.bot_data["logist_" + request_id] = logist
    context.bot_data["time_" + request_id]   = response_time_str

    jobs = context.job_queue.get_jobs_by_name("remind_" + request_id)
    for job in jobs:
        job.schedule_removal()

    kb = [
        [InlineKeyboardButton("Виконано, дякую!", callback_data="done_" + request_id + "_" + str(replied_id))],
        [InlineKeyboardButton("Не вирішено",      callback_data="reject_" + request_id + "_" + str(replied_id))],
    ]
    await context.bot.send_message(
        chat_id=master_id,
        text="Відповідь на заявку " + request_id + "\n\n" +
             info["product"] + " (#" + info["order_num"] + ")\n" +
             logist + ":\n\n" +
             (msg.text or "(медіа)") + "\n\n" +
             "Підтвердіть виконання або поверніть заявку:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

    context.job_queue.run_once(
        auto_close,
        when=AUTO_CLOSE_TIME,
        data={"request_id": request_id, "group_msg_id": info.get("group_msg_id"), "master_id": master_id},
        name="autoclose_" + request_id
    )

async def handle_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts      = q.data.split("_")
    request_id = parts[1]
    msg_id     = int(parts[2])

    msg_map       = context.bot_data.get("msg_map", {})
    info          = msg_map.get(msg_id, {})
    logist_answer = context.bot_data.get("answer_" + request_id, "—")
    logist_name   = context.bot_data.get("logist_" + request_id, "—")
    response_time = context.bot_data.get("time_" + request_id, "—")

    update_sheet_status(request_id, "Виконано")

    jobs = context.job_queue.get_jobs_by_name("autoclose_" + request_id)
    for job in jobs:
        job.schedule_removal()

    try:
        await context.bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=msg_id)
    except Exception as e:
        logger.error(f"Delete error: {e}")

    done_text = (
        "ВИКОНАНО | " + request_id + "\n" +
        "Виріб: " + info.get("product","—") + " (#" + info.get("order_num","—") + ")\n\n" +
        "Відповідь: " + logist_answer + "\n" +
        "Логіст: " + logist_name + "\n\n" +
        "Час відповіді: " + response_time + "\n" +
        "Підтверджено: " + datetime.now().strftime("%d.%m.%Y %H:%M")
    )
    try:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=done_text,
            message_thread_id=GROUP_TOPIC_DONE,
        )
    except Exception as e:
        logger.error(f"Done topic error: {e}")

    await q.edit_message_text("Заявку " + request_id + " закрито! Дякуємо за підтвердження.")

async def handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts      = q.data.split("_")
    request_id = parts[1]
    msg_id     = int(parts[2])

    context.user_data["rejecting_request_id"] = request_id
    context.user_data["rejecting_msg_id"]      = msg_id

    await q.edit_message_text(
        "Напишіть що саме не вирішено по заявці " + request_id + ":\n(Логіст отримає ваш коментар)"
    )
    return REJECT_COMMENT

async def step_reject_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment    = update.message.text.strip()
    request_id = context.user_data.get("rejecting_request_id", "")
    msg_id_str = context.user_data.get("rejecting_msg_id", 0)

    update_sheet_status(request_id, "Повернено", comment)

    jobs = context.job_queue.get_jobs_by_name("autoclose_" + request_id)
    for job in jobs:
        job.schedule_removal()

    msg_map = context.bot_data.get("msg_map", {})
    info    = msg_map.get(int(msg_id_str) if msg_id_str else 0, {})
    tag     = info.get("tag", "")

    try:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="Заявку " + request_id + " повернено\n\nКоментар: " + comment + "\n\n" + tag + " будь ласка вирішіть питання.",
            message_thread_id=GROUP_TOPIC_ID,
        )
    except Exception as e:
        logger.error(f"Reject notify error: {e}")

    await update.message.reply_text("Заявку " + request_id + " повернено логісту. Ваш коментар передано.")
    return ConversationHandler.END

async def auto_close(context: ContextTypes.DEFAULT_TYPE):
    data       = context.job.data
    request_id = data["request_id"]
    msg_id     = data.get("group_msg_id")
    master_id  = data.get("master_id")

    requests = get_open_requests()
    if request_id not in [r.get("request_id") for r in requests]:
        return

    update_sheet_status(request_id, "Виконано (авто)", "Автозакриття через 24г")

    try:
        if msg_id:
            await context.bot.delete_message(chat_id=GROUP_CHAT_ID, message_id=msg_id)
    except:
        pass

    try:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text="Автозакриття | " + request_id + "\nЗакрито через 24г без реакції бригадира.",
            message_thread_id=GROUP_TOPIC_DONE,
        )
    except:
        pass

    try:
        if master_id:
            await context.bot.send_message(
                chat_id=master_id,
                text="Заявку " + request_id + " автоматично закрито (24г без реакції)."
            )
    except:
        pass

async def my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id  = update.effective_user.id
    requests = get_user_requests(chat_id)

    if not requests:
        await update.message.reply_text("У вас немає заявок.")
        return

    open_r   = [r for r in requests if r.get("status") not in ("Виконано","Виконано (авто)")]
    closed_r = [r for r in requests if r.get("status") in ("Виконано","Виконано (авто)")]

    text = "Ваші заявки:\n\n"
    if open_r:
        text += "Відкриті:\n"
        for r in open_r[-10:]:
            status = r.get("status","")
            icon = {"Нова":"NEW","В роботі":"WIP","Повернено":"BACK"}.get(status,"?")
            text += icon + " " + r["request_id"] + " — " + r.get("department","") + " #" + str(r.get("order_num","")) + "\n"
            text += "   " + r.get("product","") + " | " + status + "\n"
    if closed_r:
        text += "\nВиконані (останні 5):\n"
        for r in closed_r[-5:]:
            text += "OK " + r["request_id"] + " — " + r.get("department","") + " #" + str(r.get("order_num","")) + "\n"

    await update.message.reply_text(text)

async def my_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uname    = update.effective_user.username or ""
    tag      = "@" + uname
    requests = get_open_requests(logist_tag=tag)

    if not requests:
        await update.message.reply_text("У вас немає відкритих заявок.")
        return

    text = "Ваші відкриті заявки (" + str(len(requests)) + "):\n\n"
    for r in requests:
        status = r.get("status","")
        icon = {"Нова":"NEW","В роботі":"WIP","Повернено":"BACK"}.get(status,"?")
        text += icon + " " + r["request_id"] + " — " + r.get("department","") + " #" + str(r.get("order_num","")) + "\n"
        text += "   " + r.get("product","") + "\n\n"

    await update.message.reply_text(text)

async def daily_digest(context: ContextTypes.DEFAULT_TYPE):
    open_requests = get_open_requests()
    if not open_requests:
        return

    if ADMIN_CHAT_ID:
        by_dept = {}
        for r in open_requests:
            dept = r.get("department","?")
            by_dept.setdefault(dept, []).append(r)

        text = "Ранковий дайджест " + datetime.now().strftime("%d.%m.%Y") + "\n\n"
        text += "Всього відкритих: " + str(len(open_requests)) + "\n\n"
        for dept, reqs in by_dept.items():
            text += dept + ": " + str(len(reqs)) + " заявок\n"
            for r in reqs[:3]:
                text += "  - " + r["request_id"] + " #" + str(r.get("order_num","")) + " " + r.get("product","") + "\n"
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
        except Exception as e:
            logger.error(f"Digest admin error: {e}")

    for uname, cid in LOGIST_CHAT_IDS.items():
        tag  = "@" + uname
        mine = get_open_requests(logist_tag=tag)
        if not mine:
            continue
        text = "Ваші відкриті заявки на " + datetime.now().strftime("%d.%m") + ":\n\n"
        for r in mine:
            text += r["request_id"] + " — " + r.get("department","") + " #" + str(r.get("order_num","")) + "\n"
            text += "   " + r.get("product","") + "\n\n"
        try:
            await context.bot.send_message(chat_id=cid, text=text)
        except Exception as e:
            logger.error(f"Digest logist error: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано. Натисніть /start")
    return ConversationHandler.END

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.job_queue.run_daily(
        daily_digest,
        time=datetime.strptime("08:00", "%H:%M").time(),
        name="daily_digest"
    )

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(handle_reject, pattern="^reject_"),
        ],
        states={
            DEPT:           [CallbackQueryHandler(step_dept,     pattern="^dept_")],
            SUB_TYPE:       [
                CallbackQueryHandler(step_subtype,  pattern="^(sub_|back_)"),
                CallbackQueryHandler(step_datesub,  pattern="^datesub_"),
            ],
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
    app.add_handler(CallbackQueryHandler(handle_done, pattern="^done_"))
    app.add_handler(CommandHandler("mytasks", my_tasks))
    app.add_handler(CommandHandler("myqueue", my_queue))
    app.add_handler(MessageHandler(
        filters.Chat(GROUP_CHAT_ID) & filters.REPLY & filters.TEXT,
        handle_reply
    ))

    logger.info("Starting bot...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

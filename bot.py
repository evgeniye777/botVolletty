import logging
import sqlite3
from typing import Optional, Tuple, List
import os

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ----------------------------
# Логирование
# ----------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ----------------------------
# Настройки
# ----------------------------
DB_PATH = "bot.db"

TOKEN = os.getenv("BOT_TOKEN")

if TOKEN is None:
    raise ValueError("The variable BOT_TOKEN must be defined in environment variables.")
else:
    logger.debug(f"BOT_TOKEN: {TOKEN}")

ADMIN_USERNAMES = ["Boss_Jendos", "Alexandr_Vellutto"]  # без @

TICKETS = [
    {"id": 1, "name": "1000 (1 билет)", "price": 100000},
    {"id": 2, "name": "2000 (2 билета)", "price": 200000},
    {"id": 3, "name": "3000 (3 билета)", "price": 300000},
    {"id": 4, "name": "4000 (4 билета)", "price": 400000},
    {"id": 5, "name": "5000 (5 билетов)", "price": 500000},
    {"id": 6, "name": "6000 (6 билетов)", "price": 600000},
    {"id": 7, "name": "7000 (7 билетов)", "price": 700000},
    {"id": 8, "name": "8000 (8 билетов)", "price": 800000},
    {"id": 9, "name": "9000 (9 билетов)", "price": 900000},
    {"id": 10, "name": "10000 (10 билетов)", "price": 1000000},
    {"id": -1, "name": "Репост(бесплатный билет)", "price": 0},
]

CARD_NUMBER = "2200 7020 1284 8458"


# ----------------------------
# DB helpers
# ----------------------------
def _connect():
    return sqlite3.connect(DB_PATH)


def init_db():
    try:
        conn = _connect()
        cursor = conn.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                fio TEXT,
                phone TEXT,
                chat_id INTEGER
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_username TEXT,
                ticket_id INTEGER,
                FOREIGN KEY (user_username) REFERENCES users (username)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_username TEXT,
                ticket_id INTEGER,
                status TEXT DEFAULT 'pending',
                screenshot_file_id TEXT,
                reason TEXT,
                FOREIGN KEY (user_username) REFERENCES users (username)
            )
            """
        )

        # Миграция: purchases coupon_id -> ticket_id
        cursor.execute("PRAGMA table_info(purchases)")
        columns = cursor.fetchall()
        if any(col[1] == "coupon_id" for col in columns):
            cursor.execute("ALTER TABLE purchases RENAME COLUMN coupon_id TO ticket_id")

        # Миграция: users.chat_id
        cursor.execute("PRAGMA table_info(users)")
        user_cols = [c[1] for c in cursor.fetchall()]
        if "chat_id" not in user_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN chat_id INTEGER")

        # Миграция: payments.reason
        cursor.execute("PRAGMA table_info(payments)")
        pay_cols = [c[1] for c in cursor.fetchall()]
        if "reason" not in pay_cols:
            cursor.execute("ALTER TABLE payments ADD COLUMN reason TEXT")

        # Проверяем, существует ли индекс
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_purchases_user_ticket'"
        )
        index_exists = cursor.fetchone() is not None

        if not index_exists:
            # Удаляем дубли перед созданием индекса
            cursor.execute(
                """
                DELETE FROM purchases
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM purchases
                    GROUP BY user_username, ticket_id
                )
                """
            )
            logger.info("Дубликаты в purchases удалены.")

            # Создаём уникальный индекс
            cursor.execute(
                "CREATE UNIQUE INDEX idx_purchases_user_ticket ON purchases(user_username, ticket_id)"
            )
            logger.info("Уникальный индекс создан.")

        conn.commit()
        conn.close()
        logger.info("База данных инициализирована.")
    except Exception as e:
        logger.exception("Ошибка инициализации БД: %s", e)


def upsert_user_chat_id(username: str, chat_id: int):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE username = ?", (username,))
    exists = cursor.fetchone() is not None
    if exists:
        cursor.execute("UPDATE users SET chat_id = ? WHERE username = ?", (chat_id, username))
    else:
        cursor.execute(
            "INSERT INTO users (username, fio, phone, chat_id) VALUES (?, NULL, NULL, ?)",
            (username, chat_id),
        )
    conn.commit()
    conn.close()


def get_user(username: str) -> Optional[Tuple[int, Optional[str], Optional[str], Optional[int]]]:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT rowid, fio, phone, chat_id FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    return row


def save_user(username: str, fio: str, phone: str, chat_id: Optional[int] = None):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO users (username, fio, phone, chat_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            fio = excluded.fio,
            phone = excluded.phone,
            chat_id = COALESCE(excluded.chat_id, users.chat_id)
        """,
        (username, fio, phone, chat_id),
    )
    conn.commit()
    conn.close()


def get_user_chat_id(username: str) -> Optional[int]:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row and row[0] is not None else None


def save_purchase(username: str, ticket_id: int):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO purchases (user_username, ticket_id) VALUES (?, ?)",
        (username, ticket_id),
    )
    conn.commit()
    conn.close()


def delete_purchase(username: str, ticket_id: int):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM purchases WHERE user_username = ? AND ticket_id = ?",
        (username, ticket_id),
    )
    conn.commit()
    conn.close()


def get_all_users() -> List[Tuple[int, str, Optional[str], Optional[str]]]:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("SELECT rowid, username, fio, phone FROM users WHERE fio IS NOT NULL ORDER BY rowid")
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_paid_users() -> List[Tuple[int, str, Optional[str], Optional[str], str]]:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT u.rowid, u.username, u.fio, u.phone, GROUP_CONCAT(p.ticket_id) as tickets
        FROM users u
        JOIN purchases p ON u.username = p.user_username
        GROUP BY u.username
        ORDER BY u.rowid
        """
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def save_payment(username: str, ticket_id: int, screenshot_file_id: str) -> Optional[int]:
    try:
        conn = _connect()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO payments (user_username, ticket_id, screenshot_file_id) VALUES (?, ?, ?)",
            (username, ticket_id, screenshot_file_id),
        )
        payment_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return payment_id
    except Exception as e:
        logger.exception("Ошибка сохранения платежа: %s", e)
        return None


def get_payment(payment_id: int) -> Optional[Tuple[str, int, str, Optional[str]]]:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_username, ticket_id, status, reason FROM payments WHERE id = ?",
        (payment_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return row


def set_payment_status(payment_id: int, status: str, reason: Optional[str] = None):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE payments SET status = ?, reason = ? WHERE id = ?",
        (status, reason, payment_id),
    )
    conn.commit()
    conn.close()


def get_pending_payments():
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT p.id, p.user_username, p.ticket_id, p.screenshot_file_id, u.fio, u.phone
        FROM payments p
        JOIN users u ON p.user_username = u.username
        WHERE p.status = 'pending'
        """
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def count_pending_payments() -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM payments WHERE status = "pending"')
    count = cursor.fetchone()[0]
    conn.close()
    return count

def get_user_tickets(username: str) -> List[Tuple[int, int, str]]:
    """
    Возвращает список билетов пользователя с их статусами.
    Возвращает: [(payment_id, ticket_id, status), ...]
    Исключает статус 'fake'.
    """
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, ticket_id, status
        FROM payments
        WHERE user_username = ? AND status != 'fake'
        ORDER BY id DESC
        """,
        (username,)
    )
    rows = cursor.fetchall()
    conn.close()
    return rows
    
def get_repost_status(username: str) -> Optional[str]:
    """
    Проверяет статус репоста (билет -1) для пользователя.
    Возвращает: 'confirmed', 'pending', 'fake' или None (если нет)
    """
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT status
        FROM payments
        WHERE user_username = ? AND ticket_id = -1
        ORDER BY id DESC
        LIMIT 1
        """,
        (username,)
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None
    
def get_lottery_data() -> List[Tuple[int, str, int, int]]:
    """
    Возвращает данные для лотереи: [(user_id, fio, total_tickets, total_reposts), ...].
    """
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT 
            u.rowid,
            u.fio,
            SUM(CASE WHEN p.ticket_id = -1 THEN 1 ELSE p.ticket_id END) AS total_tickets,
            SUM(CASE WHEN p.ticket_id = -1 THEN 1 ELSE 0 END) AS total_reposts
        FROM users u
        JOIN (SELECT DISTINCT user_username, ticket_id FROM purchases) p 
            ON u.username = p.user_username
        WHERE u.fio IS NOT NULL
        GROUP BY u.rowid, u.fio
        ORDER BY u.rowid
        """
    )
    rows = cursor.fetchall()
    conn.close()
    return rows
    
def format_lottery_text() -> str:
    """
    Форматирует список участников лотереи в текстовом виде.
    
    Returns:
        str: Отформатированный текст со списком участников.
    """
    lottery_data = get_lottery_data()
    
    if not lottery_data:
        return "📋 Список участников лотереи пуст."
    
    text = "📋 Список участников лотереи:\n\n"
    total_tickets = 0
    total_reposts = 0
    
    for user_id, fio, tickets_count, user_reposts in lottery_data:
        text += (
            f"{user_id}) {fio}\n"
            f"|    Кол-во билетов: {tickets_count}\n"
            f"{'‾' * 30}\n\n"
        )
        total_tickets += tickets_count
        total_reposts += user_reposts
    
    # Добавляем итоговую информацию
    text += (
        f"{'═' * 30}\n"
        f"📊 Всего участников: {len(lottery_data)}\n"
        f"🎫 Всего билетов: {total_tickets}\n"
        f"🎁 Всего Репостных билетов: {total_reposts}\n"
        f"📈 Среднее билетов на участника: {total_tickets / len(lottery_data):.1f}"
    )
    
    return text

# ----------------------------
# UI helpers
# ----------------------------
def is_admin(username: Optional[str]) -> bool:
    return bool(username) and username in ADMIN_USERNAMES


def ticket_name(ticket_id: int) -> str:
    t = next((x for x in TICKETS if x["id"] == ticket_id), None)
    return t["name"] if t else str(ticket_id)
    
def ticket_word(num):  # ← СЮДА
    if num % 10 == 1 and num % 100 != 11:
        return "билет"
    elif 2 <= num % 10 <= 4 and (num % 100 < 10 or num % 100 >= 20):
        return "билета"
    else:
        return "билетов"

def get_persistent_keyboard() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура с кнопкой перезапуска"""
    keyboard = [[KeyboardButton("🔄 Перезапустить бот")]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def send_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Список тех кто оплатил", callback_data="paid_list")],
        [InlineKeyboardButton("Полный список", callback_data="full_list")],
        [InlineKeyboardButton("Список непроверенных оплат", callback_data="pending_payments")],
        [InlineKeyboardButton("Список для лотереи", callback_data="lottery_list")],
        [InlineKeyboardButton("Запуск в роли клиента", callback_data="client_mode")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text("Вы организатор. Выберите действие:", reply_markup=reply_markup)
        except Exception:
            # Если не получилось — удаляем и отправляем новое
            # try:
            #     await update.callback_query.message.delete()
            # except:
            #     pass
            msg = await update.callback_query.message.reply_text("Вы организатор. Выберите действие:", reply_markup=reply_markup)
            if "bot_messages" not in context.user_data:
                context.user_data["bot_messages"] = []
            context.user_data["bot_messages"].append(msg.message_id)
    elif update.message:
        msg = await update.message.reply_text("Вы организатор. Выберите действие:", reply_markup=reply_markup)
        if "bot_messages" not in context.user_data:
            context.user_data["bot_messages"] = []
        context.user_data["bot_messages"].append(msg.message_id)

async def send_tickets_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    keyboard = []

    # Проверяем статус репоста
    repost_status = get_repost_status(username)

    # Проверяем наличие любых приобретённых билетов у пользователя
    has_any_ticket = False
    user_tickets = get_user_tickets(username)
    for _, ticket_id, _ in user_tickets:
        if ticket_id != -1:  # Любой билет, кроме репостного
            has_any_ticket = True
            break

    for ticket in TICKETS:
        if ticket['id'] == -1:
            # Это репост — обрабатываем особым образом
            if repost_status == 'confirmed':
                # Репост подтверждён — не показываем кнопку вообще
                continue
            elif repost_status == 'pending':
                # Репост на проверке — показываем некликабельную кнопку
                keyboard.append([InlineKeyboardButton("Репост (на проверке) ⏳", callback_data="repost_pending")])
            elif has_any_ticket:
                # Пользователь приобрёл хотя бы один билет → кнопка доступна
                keyboard.append([InlineKeyboardButton(f"{ticket['name']}", callback_data=f"buy_{ticket['id']}")])
            else:
                # Пользователь не купил ни одного билета → блокируем доступ к репосту
                keyboard.append([
                    InlineKeyboardButton("🔒 Репост (бесплатный билет)", callback_data="locked_repost")
                ])
        else:
            # Обычный билет
            keyboard.append([InlineKeyboardButton(f"{ticket['name']}", callback_data=f"buy_{ticket['id']}")])

    keyboard.append([InlineKeyboardButton("Мои купленные билеты", callback_data="my_tickets")])

    if is_admin(username):
        keyboard.append([InlineKeyboardButton("Вернуться к меню организатора", callback_data="back_to_admin")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        msg = await update.message.reply_text("Выберите билет для покупки:", reply_markup=reply_markup)
        if "bot_messages" not in context.user_data:
            context.user_data["bot_messages"] = []
        context.user_data["bot_messages"].append(msg.message_id)
    elif update.callback_query:
        try:
            # Пытаемся отредактировать текст
            await update.callback_query.edit_message_text("Выберите билет для покупки:", reply_markup=reply_markup)
        except Exception as e:
            # Если не получилось (это фото или caption) — удаляем и отправляем новое
            try:
                await update.callback_query.message.delete()
            except:
                pass
            msg = await update.callback_query.message.reply_text("Выберите билет для покупки:", reply_markup=reply_markup)
            if "bot_messages" not in context.user_data:
                context.user_data["bot_messages"] = []
            context.user_data["bot_messages"].append(msg.message_id)

async def notify_client(context: ContextTypes.DEFAULT_TYPE, username: str, text: str):
    chat_id = get_user_chat_id(username)
    if chat_id:
        try:
            # Добавляем кнопку "К списку билетов"
            keyboard = [[InlineKeyboardButton("📋 К списку билетов", callback_data="back_to_tickets")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление пользователю {username}: {e}")
    else:
        logger.warning(f"У пользователя {username} нет chat_id в БД.")


# ----------------------------
# Handlers
# ----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    chat_id = update.effective_chat.id
    
    # Сохраняем chat_id
    if username:
        upsert_user_chat_id(username, chat_id)
    
    # Сохраняем chat_id организаторов в bot_data для уведомлений
    if is_admin(username):
        if "admin_chat_ids" not in context.bot_data:
            context.bot_data["admin_chat_ids"] = {}
        context.bot_data["admin_chat_ids"][username] = chat_id  # ✅ Это важно!
        
        # Отправляем постоянную клавиатуру
        msg = await update.message.reply_text(
            "Добро пожаловать!",
            reply_markup=get_persistent_keyboard()
        )
        context.user_data["bot_messages"] = [msg.message_id]
        
        keyboard = [
            [InlineKeyboardButton("Список тех кто оплатил", callback_data="paid_list")],
            [InlineKeyboardButton("Полный список", callback_data="full_list")],
            [InlineKeyboardButton("Список непроверенных оплат", callback_data="pending_payments")],
            [InlineKeyboardButton("Список для лотереи", callback_data="lottery_list")],
            [InlineKeyboardButton("Запуск в роли клиента", callback_data="client_mode")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg2 = await update.message.reply_text("Вы организатор. Выберите действие:", reply_markup=reply_markup)
        context.user_data["bot_messages"].append(msg2.message_id)
    else:
        # Клиент
        user_data = get_user(username)
        if user_data and user_data[1]:  # fio заполнено (теперь индекс 1)
            user_id, fio, phone, _ = user_data
            await update.message.reply_text(
                f"Вы уже зарегистрированы.\n"
                f"Ваш уникальный номер: {user_id}\n"
                f"ФИО: {fio}\n"
                f"Номер телефона: {phone}\n\n",
                reply_markup=get_persistent_keyboard()
            )
            await send_tickets_menu(update, context)
        else:
            await update.message.reply_text(
                "Введите ваше ФИО:",
                reply_markup=get_persistent_keyboard()
            )
            context.user_data["step"] = "fio"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    text = update.message.text
    
    # Обработка кнопки перезапуска
    if text == "🔄 Перезапустить бот":
        context.user_data.clear()
        await start(update, context)
        return
    
    # Проверяем, ждём ли мы причину от организатора
    if "awaiting_fake_reason" in context.user_data and is_admin(username):
        payment_id = context.user_data["awaiting_fake_reason"]
        del context.user_data["awaiting_fake_reason"]
        
        payment = get_payment(payment_id)
        if not payment:
            await update.message.reply_text("Платёж не найден.")
            return
        
        user_username, ticket_id, old_status, _ = payment
        
        if old_status == "fake":
            await update.message.reply_text("Этот платёж уже отмечен как фейк. Повторное действие игнорируется.")
            return
        
        # Меняем статус на fake с причиной
        set_payment_status(payment_id, "fake", text)
        
        # Удаляем покупку, если была подтверждена
        if old_status == "confirmed":
            delete_purchase(user_username, ticket_id)
        
        # Если это репост (ticket_id == -1), удаляем запись из payments
        # чтобы пользователь мог отправить репост заново
        if ticket_id == -1:
            conn = _connect()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
            conn.commit()
            conn.close()
        
        # Уведомляем клиента
        if ticket_id == -1:
            # Это репост
            msg = f"Ваш репост проверен, но отклонён.\nПричина: {text}"
        else:
            # Обычный платёж
            msg = f"Ваша оплата проверена, но отклонёна.\nПричина: {text}"
        
        await notify_client(context, user_username, msg)
        
        # Добавляем кнопку "Вернуться в меню организатора"
        keyboard = [[InlineKeyboardButton("◀️ Вернуться в меню организатора", callback_data="back_to_admin")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg_sent = await update.message.reply_text(
            "Платёж отмечен как фейк с указанной причиной. Клиент уведомлён.",
            reply_markup=reply_markup
        )
        
        # Сохраняем ID сообщения для отслеживания
        if "bot_messages" not in context.user_data:
            context.user_data["bot_messages"] = []
        context.user_data["bot_messages"].append(msg_sent.message_id)
        return
    
    # Обычная логика регистрации клиента
    step = context.user_data.get("step")
    
    if step == "fio":
        context.user_data["fio"] = text
        await update.message.reply_text("Введите ваш номер телефона:")
        context.user_data["step"] = "phone"
    elif step == "phone":
        fio = context.user_data.get("fio")
        phone = text
        chat_id = update.effective_chat.id
        save_user(username, fio, phone, chat_id)
        context.user_data.clear()
        await update.message.reply_text("Регистрация завершена!")
        await send_tickets_menu(update, context)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    ticket_id = context.user_data.get("awaiting_screenshot")
    
    if not ticket_id:
        # Добавляем кнопку "К списку билетов"
        keyboard = [[InlineKeyboardButton("📋 К списку билетов", callback_data="back_to_tickets")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Сначала выберите билет для оплаты.",
            reply_markup=reply_markup
        )
        return
    
    photo = update.message.photo[-1]
    screenshot_file_id = photo.file_id
    
    # Сохраняем платёж
    payment_id = save_payment(username, ticket_id, screenshot_file_id)
    
    if not payment_id:
        await update.message.reply_text("Ошибка сохранения платежа. Попробуйте позже.")
        return
    
    # Получаем данные пользователя
    user_data = get_user(username)
    if not user_data:
        await update.message.reply_text("Ошибка: данные пользователя не найдены.")
        return
    
    user_id, fio, phone, _ = user_data
    ticket_name_str = ticket_name(ticket_id)
    
    # Считаем непроверенные платежи
    pending_count = count_pending_payments()
    
    # Отправляем организаторам
    admin_chat_ids = context.bot_data.get("admin_chat_ids", {})
    for admin_username, admin_chat_id in admin_chat_ids.items():
        keyboard = [
            [InlineKeyboardButton("Фейк", callback_data=f"fake_{payment_id}")],
            [InlineKeyboardButton("Подтвердить", callback_data=f"confirm_{payment_id}")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Проверяем, это репост или обычный платёж
        if ticket_id == -1:
            caption = (
                f"Новый репост (бесплатный билет)!\n"
                f"Уникальный номер: {user_id}\n"
                f"Пользователь: @{username}\n"
                f"ФИО: {fio}\n"
                f"Номер: {phone}\n"
                f"Билет: {ticket_name_str}"
            )
        else:
            caption = (
                f"Новый платёж!\n"
                f"Уникальный номер: {user_id}\n"
                f"Пользователь: @{username}\n"
                f"ФИО: {fio}\n"
                f"Номер: {phone}\n"
                f"Билет: {ticket_name_str}"
            )
        
        if pending_count > 1:
            caption += f"\n\nУ вас {pending_count} непроверенных платежей (включая этот)."
        
        try:
            await context.bot.send_photo(
                chat_id=admin_chat_id,
                photo=screenshot_file_id,
                caption=caption,
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.error(f"Не удалось отправить скриншот организатору {admin_username}: {e}")
    
    # Очищаем ожидание скриншота
    del context.user_data["awaiting_screenshot"]
    
    # Добавляем inline-кнопку "К списку билетов"
    keyboard = [[InlineKeyboardButton("📋 К списку билетов", callback_data="back_to_tickets")]]
    reply_markup_inline = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Скриншот отправлен на проверку организаторам. Ожидайте подтверждения.",
        reply_markup=reply_markup_inline
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    username = update.effective_user.username
    
    # --- Покупка билета ---
    if data.startswith("buy_"):
        ticket_id = int(data.split("_")[1])
        ticket = next((t for t in TICKETS if t["id"] == ticket_id), None)
        if ticket:
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back_to_tickets")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Проверяем, это репост или обычный билет
            if ticket_id == -1:
                # Это репост (бесплатный билет)
                await query.edit_message_text(
                    f"Для получения бесплатного билета:\n\n"
                    f"1. Сделайте репост розыгрыша с канала\n"
                    f"https://t.me/Vellutto_ceiling в историю Telegram\n\n"
                    f"2. Сделайте скриншот вашей истории\n\n"
                    f"3. Отправьте скриншот сюда\n\n"
                    f"4. Дождитесь подтверждения, после чего вам присвоится бесплатный билет",
                    reply_markup=reply_markup
                )
            else:
                # Обычный билет с оплатой
                await query.edit_message_text(
                    f"Для покупки билета '{ticket['name']}' переведите {ticket['price'] / 100:.2f} руб на карту:\n"
                    f"{CARD_NUMBER}\n\n"
                    f"Затем скиньте скриншот с фактом перевода СБП "
                    f"(на скрине должно быть видно время отправки и имя отправителя).",
                    reply_markup=reply_markup
                )
            
            context.user_data["awaiting_screenshot"] = ticket_id
    
    # --- Список оплативших ---
    elif data == "paid_list":
        paid_users = get_paid_users()
        
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back_to_admin")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if not paid_users:
            await query.edit_message_text("Список оплативших пуст.", reply_markup=reply_markup)
            return
        
        text = "Список тех кто оплатил:\n\n"
        for user_id, uname, fio, phone, tickets_str in paid_users:
            ticket_ids = tickets_str.split(",")
            ticket_names = []
            for tid in ticket_ids:
                tid_int = int(tid)
                if tid_int == -1:
                    # Репост — без номера билета
                    ticket_names.append(ticket_name(tid_int))
                else:
                    # Обычный билет — с номером
                    ticket_names.append(f"{ticket_name(tid_int)}")
            
            # Форматируем билеты: первый на одной строке, остальные с отступом
            if len(ticket_names) == 1:
                tickets_formatted = ticket_names[0]
            else:
                tickets_formatted = ticket_names[0] + "\n" + "\n".join(f"|                     {t}" for t in ticket_names[1:])
            
            text += (
                f"{user_id}) - @{uname}\n"
                f"|   - {fio}\n"
                f"|   - {phone}\n"
                f"|   - Билеты: {tickets_formatted}\n"
                f"{'‾' * 30}\n\n"
            )
        
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    # --- Полный список ---
    elif data == "full_list":
        all_users = get_all_users()
        
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back_to_admin")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if not all_users:
            await query.edit_message_text("Список зарегистрированных пуст.", reply_markup=reply_markup)
            return
        
        text = "Полный список зарегистрированных:\n\n"
        for user_id, uname, fio, phone in all_users:
            text += (
                f"{user_id}) - @{uname}\n"
                f"|   - {fio}\n"
                f"|   - {phone}\n"
                f"{'‾' * 30}\n\n"
            )
        
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    # --- Список непроверенных платежей ---
    elif data == "pending_payments":
        pending = get_pending_payments()
        
        keyboard_back = [[InlineKeyboardButton("◀️ Назад", callback_data="back_to_admin")]]
        reply_markup_back = InlineKeyboardMarkup(keyboard_back)
        
        if not pending:
            await query.edit_message_text("Нет непроверенных платежей.", reply_markup=reply_markup_back)
            return
        
        for payment_id, user_username, ticket_id, screenshot_file_id, fio, phone in pending:
            user_data = get_user(user_username)
            if not user_data:
                continue
            user_id = user_data[0]
    
            ticket_name_str = ticket_name(ticket_id)
            keyboard = [
                [InlineKeyboardButton("Фейк", callback_data=f"fake_{payment_id}")],
                [InlineKeyboardButton("Подтвердить", callback_data=f"confirm_{payment_id}")],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=screenshot_file_id,
                caption=f"Уникальный номер: {user_id}\nПользователь: @{user_username}\nФИО: {fio}\nНомер: {phone}\nБилет: {ticket_name_str}",
                reply_markup=reply_markup,
            )
        
        await query.edit_message_text("Все непроверенные платежи отправлены выше.", reply_markup=reply_markup_back)
    
    # --- Список для лотереи ---
    elif data == "lottery_list":
        try:
            # Получаем отформатированный текст
            lottery_text = format_lottery_text()
        
            # Кнопка "Назад"
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back_to_admin")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        
            # Telegram ограничивает длину сообщения 4096 символами
            if len(lottery_text) > 4000:
                # Если текст слишком длинный, разбиваем на части
                parts = []
                current_part = ""
            
                for line in lottery_text.split('\n'):
                    if len(current_part) + len(line) + 1 > 4000:
                        parts.append(current_part)
                        current_part = line + '\n'
                    else:
                        current_part += line + '\n'
            
                if current_part:
                    parts.append(current_part)
            
                # Отправляем первую часть с редактированием
                await query.edit_message_text(parts[0])
            
                # Остальные части отправляем отдельными сообщениями
                for part in parts[1:]:
                    await query.message.reply_text(part)
            
                # Последнее сообщение с кнопкой
                await query.message.reply_text(
                    "Конец списка",
                    reply_markup=reply_markup
                )
            else:
                # Если текст помещается в одно сообщение
                await query.edit_message_text(
                    lottery_text,
                    reply_markup=reply_markup
                )
        
            logger.info("Lottery list shown to admin")
        
        except Exception as e:
            logger.exception(f"Error showing lottery list: {e}")
            await query.message.reply_text(
                "❌ Ошибка при получении списка участников. Попробуйте позже."
            )
            await send_admin_menu(update, context)
            
    # --- Мои купленные билеты (для клиента) ---
    elif data == "my_tickets":
        user_tickets = get_user_tickets(username)
        
        if not user_tickets:
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back_to_tickets")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("У вас пока нет купленных билетов.", reply_markup=reply_markup)
            return
        
        text = "Ваши купленные билеты:\n\n"
        for i, (payment_id, ticket_id, status) in enumerate(user_tickets, 1):
            # Переводим статус на русский
            if status == "confirmed":
                status_ru = "✅ Подтверждён"
            elif status == "pending":
                status_ru = "⏳ На проверке"
            else:
                status_ru = status  # На всякий случай
            
            # Проверяем, это репост или обычный билет
            if ticket_id == -1:
                text += (
                    f"{i}) - Репост: Бесплатный билет\n"
                    f"|   - Статус: {status_ru}\n"
                    f"{'‾' * 30}\n\n"
                )
            else:
                ticket_number = ticket_id
                ticket_name_str = f"{ticket_name(ticket_id)})"
                text += (
                    f"{i}) - Покупка: {ticket_name_str}\n"
                    f"|   - Статус: {status_ru}\n"
                    f"{'‾' * 30}\n\n"
                )
        
        # Добавляем кнопку "Назад"
        keyboard = [[InlineKeyboardButton("◀️ Назад к билетам", callback_data="back_to_tickets")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup)
    
    # --- Репост на проверке (некликабельная кнопка) ---
    elif data == "repost_pending":
        await query.answer("Ваш репост уже на проверке. Ожидайте подтверждения.", show_alert=True)
        
    # --- Назад к билетам ---
    elif data == "back_to_tickets":
        await send_tickets_menu(update, context)
        
    # --- Фейк (показываем выбор причины) ---
    elif data.startswith("fake_") and not data.startswith("fake_reason_") and not data.startswith("fake_noreason_"):
        payment_id = int(data.split("_")[1])
        payment = get_payment(payment_id)
    
        if not payment:
            await query.answer("Платёж не найден.", show_alert=True)
            return
    
        user_username, ticket_id, old_status, _ = payment
    
        if old_status == "fake":
            await query.answer("Этот платёж уже отмечен как фейк.", show_alert=True)
            return
    
        # Получаем данные для нового caption
        user_data = get_user(user_username)
        if not user_data:
            await query.answer("Данные пользователя не найдены.", show_alert=True)
            return
    
        user_id,fio, phone, _ = user_data
        ticket_name_str = ticket_name(ticket_id)
    
        # Показываем кнопки выбора причины + меняем caption
        keyboard = [
            [InlineKeyboardButton("Указать причину", callback_data=f"fake_reason_{payment_id}")],
            [InlineKeyboardButton("Не указывать причину", callback_data=f"fake_noreason_{payment_id}")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
    
        new_caption = (
            f"⚠️ Выберите действие:\n\n"
            f"Уникальный номер: {user_id}\n"
            f"Пользователь: @{user_username}\n"
            f"ФИО: {fio}\n"
            f"Номер: {phone}\n"
            f"Билет: {ticket_name_str}"
        )
    
        try:
            await query.edit_message_caption(caption=new_caption, reply_markup=reply_markup)
        except Exception as e:
            logger.warning(f"Не удалось изменить сообщение: {e}")
            await query.answer()
    
    # --- Фейк с причиной (ждём текст) ---
    elif data.startswith("fake_reason_"):
        payment_id = int(data.split("_")[2])
        context.user_data["awaiting_fake_reason"] = payment_id
        await query.answer()
        await query.message.reply_text("Отправьте следующим сообщением причину отклонения.")
    
    # --- Фейк без причины ---
    elif data.startswith("fake_noreason_"):
        payment_id = int(data.split("_")[2])
        payment = get_payment(payment_id)
        
        if not payment:
            await query.answer("Платёж не найден.", show_alert=True)
            return
        
        user_username, ticket_id, old_status, _ = payment
        
        if old_status == "fake":
            await query.answer("Этот платёж уже отмечен как фейк.", show_alert=True)
            return
        
        # Меняем статус на fake без причины
        set_payment_status(payment_id, "fake", None)
        
        # Удаляем покупку, если была подтверждена
        if old_status == "confirmed":
            delete_purchase(user_username, ticket_id)
        
        # Если это репост (ticket_id == -1), удаляем запись из payments
        # чтобы пользователь мог отправить репост заново
        if ticket_id == -1:
            conn = _connect()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM payments WHERE id = ?", (payment_id,))
            conn.commit()
            conn.close()
        
        # Уведомляем клиента
        if ticket_id == -1:
            # Это репост
            msg = "Ваш репост проверен, но отклонён."
        else:
            # Обычный платёж
            msg = "Ваша оплата проверена, но отклонёна."
        
        await notify_client(context, user_username, msg)
        
        # Добавляем кнопку "Вернуться в меню организатора"
        keyboard = [[InlineKeyboardButton("◀️ Вернуться в меню организатора", callback_data="back_to_admin")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_caption(
            caption="✅ Платёж отмечен как фейк. Клиент уведомлён.",
            reply_markup=reply_markup
        )
        
    # --- Подтвердить ---
    elif data.startswith("confirm_"):
        payment_id = int(data.split("_")[1])
        payment = get_payment(payment_id)
        
        if not payment:
            await query.answer("Платёж не найден.", show_alert=True)
            return
        
        user_username, ticket_id, old_status, _ = payment
        
        if old_status == "confirmed":
            await query.answer("Этот платёж уже подтверждён.", show_alert=True)
            return
        
        # Если это репост — проверяем и очищаем дубли
        if ticket_id == -1:
            conn = _connect()
            cursor = conn.cursor()
            
            # Проверяем, есть ли уже подтверждённый репост у этого пользователя
            cursor.execute(
                """
                SELECT id FROM payments 
                WHERE user_username = ? 
                AND ticket_id = -1 
                AND status = 'confirmed'
                AND id != ?
                """,
                (user_username, payment_id)
            )
            existing_confirmed = cursor.fetchone()
            
            if existing_confirmed:
                # Уже есть подтверждённый репост — ничего не делаем
                conn.close()
                await query.answer(
                    "У этого пользователя уже есть подтверждённый репост. Подтверждение отменено.",
                    show_alert=True
                )
                
                # Обновляем caption с предупреждением
                user_data = get_user(user_username)
                if user_data:
                    user_id, fio, phone, _ = user_data
                    ticket_name_str = ticket_name(ticket_id)
                    
                    new_caption = (
                        f"Уникальный номер: {user_id}\n"
                        f"Пользователь: @{user_username}\n"
                        f"ФИО: {fio}\n"
                        f"Номер: {phone}\n"
                        f"Билет: {ticket_name_str}\n\n"
                        f"⚠️ У пользователя уже есть подтверждённый репост!"
                    )
                    
                    keyboard = [[InlineKeyboardButton("◀️ Вернуться в меню организатора", callback_data="back_to_admin")]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await query.edit_message_caption(
                        caption=new_caption,
                        reply_markup=reply_markup
                    )
                return
            
            # Удаляем все pending/fake репосты этого пользователя, кроме текущего
            cursor.execute(
                """
                DELETE FROM payments 
                WHERE user_username = ? 
                AND ticket_id = -1 
                AND id != ? 
                AND status IN ('pending', 'fake')
                """,
                (user_username, payment_id)
            )
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            
            if deleted_count > 0:
                logger.info(f"Удалено {deleted_count} дублирующихся репостов для пользователя {user_username}")
        
        # Меняем статус на confirmed
        set_payment_status(payment_id, "confirmed", None)
        
        # Добавляем покупку
        save_purchase(user_username, ticket_id)
        
        # Уведомляем клиента
        if ticket_id == -1:
            # Это репост
            msg = "Ваш репост подтверждён! Бесплатный билет активирован."
        else:
            # Обычный платёж
            msg = "Ваша оплата подтверждена."
                
        await notify_client(context, user_username, msg)
        
        # Добавляем кнопку "Вернуться в меню организатора"
        keyboard = [[InlineKeyboardButton("◀️ Вернуться в меню организатора", callback_data="back_to_admin")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_caption(
            caption="✅ Платёж подтверждён. Клиент уведомлён.",
            reply_markup=reply_markup
        )
        
    # --- Запуск в роли клиента (для организатора) ---
    elif data == "client_mode":
        user_data = get_user(username)
        if user_data and user_data[1]:  # fio заполнено
            user_id, fio, phone, _ = user_data
            
            # Проверяем статус репоста
            repost_status = get_repost_status(username)
            
            # Создаём меню билетов
            keyboard = []
            for ticket in TICKETS:
                if ticket['id'] == -1:
                    # Это репост — обрабатываем особым образом
                    if repost_status == 'confirmed':
                        # Репост подтверждён — не показываем кнопку вообще
                        continue
                    elif repost_status == 'pending':
                        # Репост на проверке — показываем некликабельную кнопку
                        keyboard.append([InlineKeyboardButton("Репост (на проверке) ⏳", callback_data="repost_pending")])
                    elif repost_status == 'fake' or repost_status is None:
                        # Репост был фейком или не отправлялся — показываем обычную кнопку
                        keyboard.append([InlineKeyboardButton(f"{ticket['name']}", callback_data=f"buy_{ticket['id']}")])
                else:
                    # Обычный билет
                    keyboard.append([InlineKeyboardButton(f"{ticket['name']}", callback_data=f"buy_{ticket['id']}")])
            
            keyboard.append([InlineKeyboardButton("Мои купленные билеты", callback_data="my_tickets")])
            keyboard.append([InlineKeyboardButton("Вернуться к меню организатора", callback_data="back_to_admin")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Объединяем приветствие и меню в одно сообщение
            await query.edit_message_text(
                f"Вы уже зарегистрированы.\nВаш уникальный номер: {user_id}\nФИО: {fio}\nНомер телефона: {phone}\n\n"
                f"Выберите билет для покупки:",
                reply_markup=reply_markup
            )
        else:
            await query.message.reply_text(
                "Введите ваше ФИО:",
                reply_markup=get_persistent_keyboard()
            )
            context.user_data["step"] = "fio"
    
    # --- Вернуться к меню организатора ---
    elif data == "back_to_admin":
        await send_admin_menu(update, context)
        
    elif data == "locked_repost":
        await query.answer("Бесплатный билет за Репост заблокирован для вас. Чтобы разблокировать данную возможность, нужно приобрести хотя бы один любой платный билет.", show_alert=True)
        keyboard = [[InlineKeyboardButton("📋 К списку билетов", callback_data="back_to_tickets")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_reply_markup(reply_markup=reply_markup)
# ----------------------------
# Main
# ----------------------------
def main():
    init_db()
    
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    logger.info("Бот запущен.")
    application.run_polling()


if __name__ == "__main__":
    main()
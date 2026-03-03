# =========================
# 📦 IMPORTS
# =========================

import os
import time
import threading
import queue
from contextlib import contextmanager
from collections import defaultdict
 # make sure to have this file for cross-instance forwarding
import psycopg2
import telebot
from telebot.types import InputMediaPhoto, InputMediaVideo
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton


# =========================
# ⚙ CONFIGURATION
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
FIRST_ADMIN_ID = os.getenv("ADMIN_ID") # replace with your Telegram ID for initial admin access


REQUIRED_MEDIA = 12
INACTIVITY_LIMIT = 6 * 60 * 60  # 6 hours

bot = telebot.TeleBot(BOT_TOKEN)
broadcast_queue = queue.Queue()
media_groups = defaultdict(list)
album_timers = {}
user_media_buffer = defaultdict(list)
user_media_timer = {}
media_buffer_lock = threading.Lock()
activation_buffer = defaultdict(int)
activation_timer = {}
activation_lock = threading.Lock()

# =========================
# 🗄 DATABASE CONNECTION
# =========================

from contextlib import contextmanager

@contextmanager
def get_connection():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except:
        conn.rollback()
        raise
    finally:
        conn.close()
# =========================
# 🧱 DATABASE INITIALIZATION
# =========================

def init_db():

    with get_connection() as conn:
        with conn.cursor() as c:

            # =========================
            # USERS TABLE
            # =========================
            c.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT UNIQUE,
                    banned BOOLEAN DEFAULT FALSE,
                    auto_banned BOOLEAN DEFAULT FALSE,
                    whitelisted BOOLEAN DEFAULT FALSE,
                    activation_media_count INTEGER DEFAULT 0,
                    total_media_sent INTEGER DEFAULT 0,
                    last_activation_time BIGINT
                )
            """)

            # =========================
            # ADMINS TABLE
            # =========================
            c.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    user_id BIGINT PRIMARY KEY
                )
            """)

            # =========================
            # MESSAGE MAP TABLE
            # =========================
            c.execute("""
                CREATE TABLE IF NOT EXISTS message_map (
                    bot_message_id BIGINT,
                    original_user_id BIGINT,
                    receiver_id BIGINT,
                    created_at BIGINT
                )
            """)

            # =========================
            # BANNED WORDS TABLE
            # =========================
            c.execute("""
                CREATE TABLE IF NOT EXISTS banned_words (
                    word TEXT PRIMARY KEY
                )
            """)

            # =========================
            # SETTINGS TABLE
            # =========================
            c.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            # Default Join Setting
            c.execute("""
                INSERT INTO settings(key, value)
                VALUES('join_open', 'true')
                ON CONFLICT DO NOTHING
            """)
            # =========================
            # 📦 DUPLICATE TRACKING
            # =========================
            c.execute("""
                CREATE TABLE IF NOT EXISTS media_duplicates (
                    file_id TEXT PRIMARY KEY,
                    first_sender BIGINT,
                    duplicate_count INTEGER DEFAULT 0
                )
            """)
            # Default Welcome Message
            c.execute("""
                INSERT INTO settings(key, value)
                VALUES('welcome_message', '👋 Welcome!\n\nPlease drop your username:')
                ON CONFLICT DO NOTHING
            """)

            c.execute("""
                INSERT INTO settings(key, value)
                VALUES('duplicate_filter', 'false')
                ON CONFLICT DO NOTHING
            """)
            # =========================
            # FIRST ADMIN INIT
            # =========================

            first_admin = FIRST_ADMIN_ID

            if first_admin:
                try:
                    first_admin = int(first_admin)

                    c.execute("""
                        INSERT INTO admins(user_id)
                        VALUES(%s)
                        ON CONFLICT DO NOTHING
                    """, (first_admin,))

                    print("First admin ensured.")

                except Exception as e:
                    print("Admin init error:", e)

# =========================
# 👤 USER EXISTENCE
# =========================
def delete_message_globally(bot_message_id):

    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT receiver_id
                FROM message_map
                WHERE bot_message_id=%s
            """, (bot_message_id,))
            rows = c.fetchall()

    for row in rows:
        try:
            bot.delete_message(row[0], bot_message_id)
        except:
            pass

    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                DELETE FROM message_map
                WHERE bot_message_id=%s
            """, (bot_message_id,))
def purge_user_messages(user_id):

    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT bot_message_id, receiver_id
                FROM message_map
                WHERE original_user_id=%s
            """, (user_id,))
            rows = c.fetchall()

    for bot_msg_id, receiver_id in rows:
        try:
            bot.delete_message(receiver_id, bot_msg_id)
        except:
            pass

    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                DELETE FROM message_map
                WHERE original_user_id=%s
            """, (user_id,))

def get_original_sender(bot_message_id):

    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT original_user_id
                FROM message_map
                WHERE bot_message_id=%s
                LIMIT 1
            """, (bot_message_id,))
            row = c.fetchone()

    return row[0] if row else None

def user_exists(user_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT 1 FROM users WHERE user_id=%s",
                (user_id,)
            )
            return c.fetchone() is not None


def add_user(user_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO users(user_id)
                VALUES(%s)
                ON CONFLICT DO NOTHING
            """, (user_id,))
# =========================
# 🏷 USERNAME HELPERS
# =========================

def get_username(user_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT username FROM users WHERE user_id=%s",
                (user_id,)
            )
            row = c.fetchone()
            return row[0] if row else None


def set_username(user_id, username):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                UPDATE users
                SET username=%s
                WHERE user_id=%s
            """, (username.lower(), user_id))


def username_taken(username):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT 1 FROM users WHERE username=%s",
                (username.lower(),)
            )
            return c.fetchone() is not None
# =========================
# 👑 ADMIN HELPERS
# =========================

def is_admin(user_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT 1 FROM admins WHERE user_id=%s",
                (user_id,)
            )
            return c.fetchone() is not None


def add_admin(user_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO admins(user_id)
                VALUES(%s)
                ON CONFLICT DO NOTHING
            """, (user_id,))


def remove_admin(user_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "DELETE FROM admins WHERE user_id=%s",
                (user_id,)
            )
def build_prefix(user_id):

    username = get_username(user_id)

    if username:
        return f"{username}~\n"

    return "👤 Unknown\n"

# =========================
# 🚫 BAN HELPERS
# =========================

def is_banned(user_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT banned FROM users WHERE user_id=%s",
                (user_id,)
            )
            row = c.fetchone()
            return row and row[0]


def ban_user(user_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE users SET banned=TRUE WHERE user_id=%s",
                (user_id,)
            )


def unban_user(user_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE users SET banned=FALSE WHERE user_id=%s",
                (user_id,)
            )
# =========================
# ⭐ WHITELIST HELPERS
# =========================

def is_whitelisted(user_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT whitelisted FROM users WHERE user_id=%s",
                (user_id,)
            )
            row = c.fetchone()
            return row and row[0]


def whitelist_user(user_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE users SET whitelisted=TRUE WHERE user_id=%s",
                (user_id,)
            )


def remove_whitelist(user_id):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE users SET whitelisted=FALSE WHERE user_id=%s",
                (user_id,)
            )
# =========================
# 🚪 JOIN CONTROL
# =========================

def is_join_open():
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT value FROM settings WHERE key='join_open'"
            )
            row = c.fetchone()
            return row and row[0] == "true"


def set_join_status(status: bool):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                UPDATE settings
                SET value=%s
                WHERE key='join_open'
            """, ("true" if status else "false",))
# =========================
# 🧠 USER STATE RESOLVER
# =========================

def get_user_state(user_id):

    if is_admin(user_id):
        return "ADMIN"

    if is_banned(user_id):
        return "BANNED"

    if is_whitelisted(user_id):
        return "ACTIVE"

    username = get_username(user_id)

    if username is None:
        return "NO_USERNAME"

    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT auto_banned, last_activation_time
                FROM users
                WHERE user_id=%s
            """, (user_id,))
            row = c.fetchone()

    if not row:
        return "JOINING"

    auto_banned, last_activation_time = row

    if auto_banned:
        return "INACTIVE"

    if last_activation_time is None:
        return "JOINING"

    return "ACTIVE"
# =========================
# 📊 GET ACTIVATION DATA
# =========================

def get_activation_data(user_id):
    """
    Returns:
        activation_media_count,
        total_media_sent,
        auto_banned,
        last_activation_time
    """

    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                SELECT activation_media_count,
                       total_media_sent,
                       auto_banned,
                       last_activation_time
                FROM users
                WHERE user_id=%s
            """, (user_id,))
            return c.fetchone()
# =========================
# 📈 INCREMENT MEDIA
# =========================

def increment_media(user_id, amount=1):

    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                UPDATE users
                SET activation_media_count = activation_media_count + %s,
                    total_media_sent = total_media_sent + %s
                WHERE user_id=%s
            """, (amount, amount, user_id))
#========================
#wellcome msg by admin helper
#========================   
def get_welcome_message():
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT value FROM settings WHERE key='welcome_message'"
            )
            row = c.fetchone()
            return row[0] if row else "👋 Welcome!"

def set_welcome_message(text):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                UPDATE settings
                SET value=%s
                WHERE key='welcome_message'
            """, (text,))
# =========================
# 🔄 ACTIVATE USER
# =========================

def activate_user(user_id):

    now = int(time.time())

    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                UPDATE users
                SET activation_media_count = 0,
                    auto_banned = FALSE,
                    last_activation_time = %s
                WHERE user_id=%s
            """, (now, user_id))
# =========================
# ✅ CHECK ACTIVATION
# =========================

def check_activation(user_id):

    data = get_activation_data(user_id)

    if not data:
        return False

    activation_count, _, _, _ = data

    if activation_count >= REQUIRED_MEDIA:
        activate_user(user_id)
        return True

    return False
# =========================
# ⏳ AUTO INACTIVITY CHECK
# =========================

def auto_ban_inactive_users():

    limit = int(time.time()) - INACTIVITY_LIMIT

    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                UPDATE users
                SET auto_banned = TRUE,
                    activation_media_count = 0
                WHERE auto_banned = FALSE
                  AND last_activation_time IS NOT NULL
                  AND last_activation_time < %s
            """, (limit,))
            
def is_duplicate_filter_enabled():
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT value FROM settings WHERE key='duplicate_filter'"
            )
            row = c.fetchone()
            return row and row[0] == "true"


def set_duplicate_filter(status: bool):
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("""
                UPDATE settings
                SET value=%s
                WHERE key='duplicate_filter'
            """, ("true" if status else "false",))


def check_and_register_duplicate(file_id, sender_id):
    """
    Returns True if duplicate
    Returns False if first time
    """

    with get_connection() as conn:
        with conn.cursor() as c:

            c.execute(
                "SELECT 1 FROM media_duplicates WHERE file_id=%s",
                (file_id,)
            )
            exists = c.fetchone()

            if exists:
                c.execute("""
                    UPDATE media_duplicates
                    SET duplicate_count = duplicate_count + 1
                    WHERE file_id=%s
                """, (file_id,))
                return True

            else:
                c.execute("""
                    INSERT INTO media_duplicates(file_id, first_sender)
                    VALUES(%s, %s)
                """, (file_id, sender_id))
                return False
# =========================
# 🚪 START COMMAND
# =========================

@bot.message_handler(commands=['start'])
def start_command(message):

    user_id = message.chat.id

    # 🚫 Manual Ban
    if is_banned(user_id):
        bot.send_message(user_id, "🚫 You are banned.")
        return

    # 👑 Admin Auto Registration
    if is_admin(user_id):
        if not user_exists(user_id):
            add_user(user_id)

        if get_username(user_id) is None:
            set_username(user_id, "admin")

        bot.send_message(user_id, "👑 Admin access granted.")
        return

    # 🆕 New User
    if not user_exists(user_id):

        if not is_join_open():
            bot.send_message(
                user_id,
                "🚪 Joining is currently closed."
            )
            return

        add_user(user_id)

    # 🏷 Ask Username If Not Set
    if get_username(user_id) is None:
        bot.send_message(
            user_id,
            get_welcome_message()
        )
        return

    # 🧠 Show Current State
    state = get_user_state(user_id)

    if state == "JOINING":
        bot.send_message(
            user_id,
            f"🔒 Send {REQUIRED_MEDIA} media to join."
        )

    elif state == "INACTIVE":
        bot.send_message(
            user_id,
            f"⏳ You are inactive.\nSend {REQUIRED_MEDIA} media to reactivate."
        )

    else:
        bot.send_message(user_id, "👋 Welcome back!")
# =========================
# 🏷 USERNAME CAPTURE
# =========================

@bot.message_handler(
    func=lambda m: get_username(m.chat.id) is None,
    content_types=['text']
)
def capture_username(message):

    user_id = message.chat.id
    username = message.text.strip().lower()

    # Prevent commands being treated as username
    if username.startswith('/'):
        return

    if len(username) < 3:
        bot.send_message(user_id, "Username too short. Try again.")
        return

    if username_taken(username):
        bot.send_message(user_id, "Username already taken. Try another.")
        return

    set_username(user_id, username)

    bot.send_message(
        user_id,
        f"✅ {username} set.\n\nNow send {REQUIRED_MEDIA} media to join."
    )
# =========================
# 🚫 BANNED WORD CHECK
# =========================

def contains_banned_word(text):

    if not text:
        return False

    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute("SELECT word FROM banned_words")
            words = [row[0] for row in c.fetchall()]

    text = text.lower()

    for word in words:
        if word in text:
            return True

    return False
# =========================
# 🔒 HANDLE RESTRICTIONS
# =========================

def handle_restrictions(message):

    user_id = message.chat.id
    state = get_user_state(user_id)

    # 🚫 Manual Ban
    if state == "BANNED":
        bot.send_message(user_id, "🚫 You are banned.")
        return True

    # 👑 Admin Bypass
    if state == "ADMIN":
        return False

    # ⭐ Whitelisted = Always Active
    if is_whitelisted(user_id):
        return False

    # 🚫 Word Filter (

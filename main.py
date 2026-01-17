import os
import re
import asyncio
import aiosqlite
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

# =========================
# CONFIG (Render ENV)
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)

PAYMENT_CHAT_ID = int(os.getenv("PAYMENT_CHAT_ID", "0") or 0)
PAYMENT_THREAD_ID = int(os.getenv("PAYMENT_THREAD_ID", "0") or 0)
PAYMENT_TOPIC_LINK = os.getenv("PAYMENT_TOPIC_LINK", "").strip()

# Render Persistent Disk (gợi ý mount: /var/data)
DB_PATH = os.getenv("DB_PATH", "/var/data/pay_codes.sqlite3").strip()

# Webhook mode (tuỳ chọn)
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()  # ví dụ: https://xxx.onrender.com
PORT = int(os.getenv("PORT", "10000") or 10000)

PAY_RE = re.compile(r"^P\d{7}$")  # /pay P1234567


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mention_user(user_id: int, full_name: str) -> str:
    name = (full_name or "User").replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def norm_username(u: str | None) -> str:
    return (u or "").strip().lstrip("@")


async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS pay_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            chat_id INTEGER NOT NULL,
            thread_id INTEGER NOT NULL,
            pay_message_id INTEGER NOT NULL,

            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,

            code TEXT NOT NULL,
            attempt_no INTEGER NOT NULL,
            created_at TEXT NOT NULL,

            done INTEGER NOT NULL DEFAULT 0,
            done_at TEXT,
            done_by INTEGER
        )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pay_code ON pay_codes(code)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pay_user ON pay_codes(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_pay_done ON pay_codes(done)")
        await db.commit()


async def get_next_attempt_no(db, user_id: int) -> int:
    cur = await db.execute("SELECT COUNT(*) FROM pay_codes WHERE user_id=?", (user_id,))
    row = await cur.fetchone()
    return int(row[0] or 0) + 1


async def get_last_identity(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT full_name, username
            FROM pay_codes
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None, None
        return (row[0] or ""), (row[1] or "")


def is_admin(update: Update) -> bool:
    u = update.effective_user
    return bool(u and ADMIN_CHAT_ID and u.id == ADMIN_CHAT_ID)


def is_private(update: Update) -> bool:
    c = update.effective_chat
    return bool(c and c.type == "private")


def is_payment_topic(update: Update) -> bool:
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat:
        return False

    if chat.type not in ("group", "supergroup"):
        return False
    if not PAYMENT_CHAT_ID or chat.id != PAYMENT_CHAT_ID:
        return False
    if not getattr(msg, "is_topic_message", False):
        return False
    if not PAYMENT_THREAD_ID or msg.message_thread_id != PAYMENT_THREAD_ID:
        return False

    return True


def should_ignore(update: Update) -> bool:
    # private luôn xử lý (redirect)
    if is_private(update):
        return False
    # group mà không đúng topic payment thì ignore
    return not is_payment_topic(update)


async def redirect_private(update: Update):
    await update.effective_message.reply_text(
        "<b>Không hỗ trợ tại đây!</b>\n"
        "<blockquote>"
        "• Vui lòng tham gia group để tiếp tục: "
        f"<a href='{PAYMENT_TOPIC_LINK}'>Tham gia</a>\n\n"
        "• Lệnh: /pay + mã rút tiền\n"
        "• Ví dụ: <code>/pay P0321669</code>"
        "</blockquote>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_private(update):
        if is_admin(update):
            await update.effective_message.reply_text("Admin work ✅")
        else:
            await redirect_private(update)
        return

    if should_ignore(update):
        return

    await update.effective_message.reply_text(
        "✅ Payment topic.\nCú pháp: <code>/pay P1234567</code> (P + 7 số)",
        parse_mode=ParseMode.HTML,
    )


async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Private
    if is_private(update):
        if is_admin(update):
            await update.effective_message.reply_text(
                "Admin chat riêng không nhận /pay.\n"
                "User phải nhập /pay trong Topic Payment."
            )
        else:
            await redirect_private(update)
        return

    if should_ignore(update):
        return

    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    thread_id = int(getattr(msg, "message_thread_id", 0) or 0)

    warn_text = (
        "<b>Lệnh của bạn không hợp lệ!</b>"
        "<blockquote>"
        "• Lệnh: <code>/pay</code> + mã rút tiền\n"
        "• Ví dụ: <code>/pay P0321669</code>"
        "</blockquote>"
    )

    if not context.args or len(context.args) != 1:
        await msg.reply_text(warn_text, parse_mode=ParseMode.HTML)
        return

    code = (context.args[0] or "").strip()
    if not PAY_RE.match(code):
        await msg.reply_text(warn_text, parse_mode=ParseMode.HTML)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        attempt_no = await get_next_attempt_no(db, user.id)

        cur = await db.execute(
            """
            INSERT INTO pay_codes(chat_id, thread_id, pay_message_id, user_id, username, full_name,
                                  code, attempt_no, created_at, done)
            VALUES (?,?,?,?,?,?,?,?,?,0)
            """,
            (
                int(chat.id),
                int(thread_id),
                int(msg.message_id),
                int(user.id),
                norm_username(user.username),
                user.full_name or "",
                code,
                attempt_no,
                now_iso(),
            ),
        )
        pay_id = cur.lastrowid
        await db.commit()

    await msg.reply_text(
        "<b>Mã thanh toán của bạn đã được ghi nhận!</b>\n"
        "<blockquote>Vui lòng chờ admin duyệt. Tuyệt đối không spam gửi trùng mã để tránh lỗi xử lý.</blockquote>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

    if ADMIN_CHAT_ID:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Payment confirmation", callback_data=f"pay_done:{pay_id}")]]
        )

        last_full_name, last_username = await get_last_identity(user.id)
        cur_full_name = user.full_name or ""
        cur_username = norm_username(user.username)
        last_username_norm = norm_username(last_username)

        name_changed = bool(last_full_name) and (last_full_name != cur_full_name)
        username_changed = bool(last_username) and (last_username_norm != cur_username)

        warn_lines = []
        if name_changed:
            warn_lines.append(
                "⚠️ <b>Cảnh báo:</b> User có dấu hiệu <b>đổi tên</b>\n"
                f"• Trước: <code>{last_full_name}</code>\n"
                f"• Nay: <code>{cur_full_name}</code>"
            )
        if username_changed:
            warn_lines.append(
                "⚠️ <b>Cảnh báo:</b> User có dấu hiệu <b>đổi username</b>\n"
                f"• Trước: <code>@{last_username_norm}</code>\n"
                f"• Nay: <code>@{cur_username}</code>"
            )
        warn_block = ("\n\n" + "\n".join(warn_lines)) if warn_lines else ""

        admin_text = (
            "📥 <b>Pay Requests</b>\n"
            f"• ID: <code>{pay_id}</code>\n"
            f"• Code: <code>{code}</code>\n"
            f"• Attempt: <b>{attempt_no}</b>\n"
            f"• User: {mention_user(user.id, cur_full_name)}\n"
            f"• Username: @{cur_username if cur_username else '(none)'}\n"
            f"• User ID: <code>{user.id}</code>\n"
            f"• Group: <code>{chat.id}</code>\n"
            f"• Thread: <code>{thread_id}</code>\n"
            f"• Time: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
            f"{warn_block}"
        )

        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=admin_text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        except Exception as e:
            print("Send to admin failed:", repr(e))


async def on_pay_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    if not q.from_user or q.from_user.id != ADMIN_CHAT_ID:
        await q.answer("No permission.", show_alert=True)
        return

    data = q.data or ""
    if not data.startswith("pay_done:"):
        await q.answer()
        return

    try:
        pay_id = int(data.split(":", 1)[1])
    except Exception:
        await q.answer("Bad data", show_alert=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT id, chat_id, thread_id, pay_message_id, user_id, full_name, code, done
            FROM pay_codes WHERE id=?
            """,
            (pay_id,),
        )
        row = await cur.fetchone()
        if not row:
            await q.answer("Không tìm thấy record.", show_alert=True)
            return

        (_id, chat_id, thread_id, pay_msg_id, user_id, full_name, code, done) = row

        if int(done) == 1:
            await q.answer("Mã này đã Done rồi.", show_alert=True)
            return

        await db.execute(
            "UPDATE pay_codes SET done=1, done_at=?, done_by=? WHERE id=?",
            (now_iso(), ADMIN_CHAT_ID, pay_id),
        )
        await db.commit()

    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await q.answer("✅ Marked as Done!")

    text = (
        "<b>Đã thanh toán thành công!</b>\n"
        "<blockquote>"
        f"• Code: <code>{code}</code>\n"
        f"• User: {mention_user(int(user_id), full_name)}"
        "</blockquote>"
    )

    # trả lời vào đúng topic + reply vào tin /pay nếu được
    try:
        await context.bot.send_message(
            chat_id=int(chat_id),
            message_thread_id=int(thread_id),
            text=text,
            parse_mode=ParseMode.HTML,
            reply_to_message_id=int(pay_msg_id),
            disable_web_page_preview=True,
        )
    except Exception as e:
        print("Send to payment topic failed:", repr(e))
        await context.bot.send_message(
            chat_id=int(chat_id),
            message_thread_id=int(thread_id),
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


async def listpay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or not is_admin(update):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT code, user_id, full_name, username, created_at, done
            FROM pay_codes
            ORDER BY id DESC
        """)
        rows = await cur.fetchall()

    if not rows:
        await update.effective_message.reply_text("Chưa có mã nào.")
        return

    total = len(rows)
    await update.effective_message.reply_text(
        f"🗂 <b>DANH SÁCH PAY (FULL)</b>\nTổng: <b>{total}</b> mã",
        parse_mode=ParseMode.HTML,
    )

    MAX_LEN = 3800
    buf = ""
    part = 1

    def fmt_row(code, user_id, full_name, username, created_at, done):
        st = "DONE" if int(done) == 1 else "PENDING"
        user_mention = mention_user(int(user_id), full_name)
        uname = f"@{norm_username(username)}" if username else ""
        return f"• <code>{code}</code> — {user_mention} {uname} — <b>{st}</b> — {created_at}\n"

    for r in rows:
        line = fmt_row(*r)
        if len(buf) + len(line) > MAX_LEN:
            await update.effective_message.reply_text(
                f"<b>Trang {part}</b>\n{buf}",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            part += 1
            buf = ""
            await asyncio.sleep(0.6)

        buf += line

    if buf.strip():
        await update.effective_message.reply_text(
            f"<b>Trang {part}</b>\n{buf}",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


async def post_init(app: Application):
    await init_db()


def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ Missing BOT_TOKEN (set ENV on Render).")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pay", pay))
    app.add_handler(CommandHandler("listpay", listpay))
    app.add_handler(CallbackQueryHandler(on_pay_done, pattern=r"^pay_done:\d+$"))

    # ========= MODE =========
    if WEBHOOK_URL:
        # Webhook mode: Render chạy service web
        # Lưu ý: WEBHOOK_URL = base url (không có /webhook) hoặc có cũng được, dưới mình set path "/webhook"
        webhook_path = "/webhook"
        full_webhook_url = WEBHOOK_URL.rstrip("/") + webhook_path

        print("✅ Bot is running (webhook)...", full_webhook_url)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=full_webhook_url,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        # Polling mode: đơn giản nhất, dùng Background Worker
        print("✅ Bot is running (polling)...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

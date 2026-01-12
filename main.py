import os
import re
import asyncio
from datetime import datetime, timezone

import asyncpg
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

# =========================
# CONFIG (ENV on Railway)
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)

PAYMENT_CHAT_ID = int(os.getenv("PAYMENT_CHAT_ID", "0") or 0)
PAYMENT_THREAD_ID = int(os.getenv("PAYMENT_THREAD_ID", "0") or 0)
PAYMENT_TOPIC_LINK = os.getenv("PAYMENT_TOPIC_LINK", "")

DATABASE_URL = os.getenv("DATABASE_URL", "")

PAY_RE = re.compile(r"^P\d{7}$")  # /pay P1234567


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mention_user(user_id: int, full_name: str) -> str:
    name = (full_name or "User").replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def norm_username(u: str | None) -> str:
    return (u or "").strip().lstrip("@")


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
    if chat.id != PAYMENT_CHAT_ID:
        return False
    if not getattr(msg, "is_topic_message", False):
        return False
    if int(msg.message_thread_id or 0) != PAYMENT_THREAD_ID:
        return False

    return True


def should_ignore(update: Update) -> bool:
    if is_private(update):
        return False
    return not is_payment_topic(update)


async def redirect_private(update: Update):
    await update.effective_message.reply_text(
        "<b>Không hỗ trợ tại đây!</b>\n"
        "<blockquote>"
        "• Vui lòng tham gia group để tiếp tục: "
        f"<a href='{PAYMENT_TOPIC_LINK}'>Tham gia</a>\n\n"
        "• Lệnh: <code>/pay PXXXXXXX</code>\n"
        "• Ví dụ: <code>/pay P0321669</code>"
        "</blockquote>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# =========================
# DB (PostgreSQL / asyncpg)
# =========================
async def init_db(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS pay_codes (
            id BIGSERIAL PRIMARY KEY,

            chat_id BIGINT NOT NULL,
            thread_id BIGINT NOT NULL,
            pay_message_id BIGINT NOT NULL,

            user_id BIGINT NOT NULL,
            username TEXT,
            full_name TEXT,

            code TEXT NOT NULL,
            attempt_no INT NOT NULL,

            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            done BOOLEAN NOT NULL DEFAULT FALSE,
            done_at TIMESTAMPTZ,
            done_by BIGINT
        );
        """)

        await conn.execute("CREATE INDEX IF NOT EXISTS idx_pay_code ON pay_codes(code);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_pay_user ON pay_codes(user_id);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_pay_done ON pay_codes(done);")


async def get_next_attempt_no(pool: asyncpg.Pool, user_id: int) -> int:
    async with pool.acquire() as conn:
        cnt = await conn.fetchval("SELECT COUNT(*) FROM pay_codes WHERE user_id=$1;", int(user_id))
        return int(cnt or 0) + 1


async def get_last_identity(pool: asyncpg.Pool, user_id: int):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT full_name, username
            FROM pay_codes
            WHERE user_id=$1
            ORDER BY id DESC
            LIMIT 1;
        """, int(user_id))
        if not row:
            return None, None
        return (row["full_name"] or ""), (row["username"] or "")


def get_pool(app: Application) -> asyncpg.Pool:
    pool = app.bot_data.get("db_pool")
    if not pool:
        raise RuntimeError("DB pool not initialized")
    return pool


async def post_init(app: Application):
    if not BOT_TOKEN:
        raise SystemExit("❌ Missing BOT_TOKEN env.")
    if not DATABASE_URL:
        raise SystemExit("❌ Missing DATABASE_URL (add PostgreSQL on Railway).")

    # Railway DATABASE_URL thường ok với asyncpg
    pool = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=1,
        max_size=5,
        command_timeout=30,
    )
    app.bot_data["db_pool"] = pool
    await init_db(pool)


async def post_shutdown(app: Application):
    pool = app.bot_data.get("db_pool")
    if pool:
        await pool.close()


# =========================
# COMMANDS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_private(update):
        if is_admin(update):
            await update.effective_message.reply_text("Admin work")
        else:
            await redirect_private(update)
        return

    if should_ignore(update):
        return

    await update.effective_message.reply_text(
        "✅ Payment topic.\n"
        "Cú pháp: /pay P1234567 (P + 7 số)"
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
        "⚠️ <b>LỆNH KHÔNG HỢP LỆ</b>\n"
        "<blockquote>"
        "• Cú pháp: <code>/pay PXXXXXXX</code>\n"
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

    pool = get_pool(context.application)
    attempt_no = await get_next_attempt_no(pool, user.id)

    async with pool.acquire() as conn:
        pay_id = await conn.fetchval("""
            INSERT INTO pay_codes(
                chat_id, thread_id, pay_message_id,
                user_id, username, full_name,
                code, attempt_no, created_at, done
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,NOW(),FALSE)
            RETURNING id;
        """,
        int(chat.id),
        int(thread_id),
        int(msg.message_id),
        int(user.id),
        norm_username(user.username),
        user.full_name or "",
        code,
        int(attempt_no)
        )

    await msg.reply_text(
        "✅ <b>LƯU THÀNH CÔNG</b>\n"
        "<blockquote>"
        "Mã thanh toán của bạn đã được ghi nhận.\n"
        "Vui lòng chờ admin duyệt.\n\n"
        "⚠️ Không spam gửi trùng mã để tránh lỗi xử lý (có thể bị trừ tiền/không duyệt)."
        "</blockquote>",
        parse_mode=ParseMode.HTML
    )

    # Notify admin with button
    if ADMIN_CHAT_ID:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Mark Done", callback_data=f"pay_done:{pay_id}")]
        ])

        last_full_name, last_username = await get_last_identity(pool, user.id)
        cur_full_name = user.full_name or ""
        cur_username = norm_username(user.username)
        last_username_norm = norm_username(last_username)

        name_changed = bool(last_full_name) and (last_full_name != cur_full_name)
        username_changed = bool(last_username) and (last_username_norm != cur_username)

        warn_lines = []
        if name_changed:
            warn_lines.append(
                "⚠️ <b>Cảnh báo:</b> User có dấu hiệu <b>đổi tên</b>\n"
                f"   • Trước: <code>{last_full_name}</code>\n"
                f"   • Nay: <code>{cur_full_name}</code>"
            )
        if username_changed:
            warn_lines.append(
                "⚠️ <b>Cảnh báo:</b> User có dấu hiệu <b>đổi username</b>\n"
                f"   • Trước: <code>@{last_username_norm}</code>\n"
                f"   • Nay: <code>@{cur_username}</code>"
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

    pool = get_pool(context.application)

    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT id, chat_id, thread_id, pay_message_id, user_id, full_name, code, done
            FROM pay_codes
            WHERE id=$1;
        """, int(pay_id))

        if not row:
            await q.answer("Không tìm thấy record.", show_alert=True)
            return

        if bool(row["done"]):
            await q.answer("Mã này đã Done rồi.", show_alert=True)
            return

        await conn.execute("""
            UPDATE pay_codes
            SET done=TRUE, done_at=NOW(), done_by=$1
            WHERE id=$2;
        """, int(ADMIN_CHAT_ID), int(pay_id))

    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await q.answer("✅ Marked as Done!")

    chat_id = int(row["chat_id"])
    thread_id = int(row["thread_id"])
    pay_msg_id = int(row["pay_message_id"])
    user_id = int(row["user_id"])
    full_name = row["full_name"] or "User"
    code = row["code"]

    text = (
        "✅ <b>PAY ĐÃ DUYỆT</b>\n"
        "<blockquote>"
        f"• Code: <code>{code}</code>\n"
        f"• User: {mention_user(user_id, full_name)}\n"
        "• Trạng thái: <b>DONE</b>"
        "</blockquote>"
    )

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_to_message_id=pay_msg_id,
            disable_web_page_preview=True,
        )
    except Exception as e:
        print("Send to payment topic failed:", repr(e))
        await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


# =========================
# ADMIN: LISTPAY (private only)
# =========================
async def listpay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or not is_admin(update):
        return

    pool = get_pool(context.application)

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT code, user_id, full_name, username, created_at, done
            FROM pay_codes
            ORDER BY id DESC;
        """)

    if not rows:
        await update.effective_message.reply_text("Chưa có mã nào.")
        return

    total = len(rows)
    await update.effective_message.reply_text(
        f"🗂 <b>DANH SÁCH PAY (FULL)</b>\nTổng: <b>{total}</b> mã",
        parse_mode=ParseMode.HTML
    )

    MAX_LEN = 3800
    buf = ""
    part = 1

    def fmt_row(r):
        st = "DONE" if bool(r["done"]) else "PENDING"
        user_mention = mention_user(int(r["user_id"]), r["full_name"] or "User")
        uname = f"@{norm_username(r['username'])}" if r["username"] else ""
        created = r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if r["created_at"] else ""
        return f"• <code>{r['code']}</code> — {user_mention} {uname} — <b>{st}</b> — {created}\n"

    for r in rows:
        line = fmt_row(r)
        if len(buf) + len(line) > MAX_LEN:
            await update.effective_message.reply_text(
                f"<b>Trang {part}</b>\n{buf}",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            part += 1
            buf = ""
            await asyncio.sleep(0.6)
        buf += line

    if buf.strip():
        await update.effective_message.reply_text(
            f"<b>Trang {part}</b>\n{buf}",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )


def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ Bạn chưa set BOT_TOKEN trên Railway Variables.")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pay", pay))
    app.add_handler(CommandHandler("listpay", listpay))
    app.add_handler(CallbackQueryHandler(on_pay_done, pattern=r"^pay_done:\d+$"))

    print("✅ Bot is running (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

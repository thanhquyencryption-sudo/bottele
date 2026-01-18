import os
import re
import asyncio
import asyncpg
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

# ✅ aiohttp routes for healthcheck
from aiohttp import web

# =========================
# CONFIG (Render ENV)
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)

PAYMENT_CHAT_ID = int(os.getenv("PAYMENT_CHAT_ID", "0") or 0)
PAYMENT_THREAD_ID = int(os.getenv("PAYMENT_THREAD_ID", "0") or 0)
PAYMENT_TOPIC_LINK = os.getenv("PAYMENT_TOPIC_LINK", "").strip()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
PORT = int(os.getenv("PORT", "10000") or 10000)

PAY_RE = re.compile(r"^P\d{7}$")  # /pay P1234567

# Global pool
DB_POOL: asyncpg.Pool | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mention_user(user_id: int, full_name: str) -> str:
    name = (full_name or "User").replace("<", "").replace(">", "")
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def norm_username(u: str | None) -> str:
    return (u or "").strip().lstrip("@")


async def init_db():
    global DB_POOL
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL")

    DB_POOL = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=1,
        max_size=5,
        command_timeout=30,
    )

    async with DB_POOL.acquire() as conn:
        await conn.execute("select 1;")


async def get_next_attempt_no(user_id: int) -> int:
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        cnt = await conn.fetchval("select count(*) from pay_codes where user_id=$1", user_id)
        return int(cnt or 0) + 1


async def get_last_identity(user_id: int):
    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            """
            select full_name, username
            from pay_codes
            where user_id=$1
            order by id desc
            limit 1
            """,
            user_id,
        )
        if not row:
            return None, None
        return (row["full_name"] or ""), (row["username"] or "")


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
    if is_private(update):
        return False
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

    attempt_no = await get_next_attempt_no(user.id)

    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        pay_id = await conn.fetchval(
            """
            insert into pay_codes(
              chat_id, thread_id, pay_message_id,
              user_id, username, full_name,
              code, attempt_no, created_at, done
            )
            values ($1,$2,$3,$4,$5,$6,$7,$8, now(), false)
            returning id
            """,
            int(chat.id),
            int(thread_id),
            int(msg.message_id),
            int(user.id),
            norm_username(user.username),
            user.full_name or "",
            code,
            int(attempt_no),
        )

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

    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            """
            select id, chat_id, thread_id, pay_message_id, user_id, full_name, code, done
            from pay_codes
            where id=$1
            """,
            pay_id,
        )
        if not row:
            await q.answer("Không tìm thấy record.", show_alert=True)
            return

        if bool(row["done"]):
            await q.answer("Mã này đã Done rồi.", show_alert=True)
            return

        await conn.execute(
            "update pay_codes set done=true, done_at=now(), done_by=$1 where id=$2",
            ADMIN_CHAT_ID,
            pay_id,
        )

    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await q.answer("✅ Marked as Done!")

    chat_id = int(row["chat_id"])
    thread_id = int(row["thread_id"])
    pay_msg_id = int(row["pay_message_id"])
    user_id = int(row["user_id"])
    full_name = row["full_name"] or ""
    code = row["code"] or ""

    text = (
        "<b>Đã thanh toán thành công!</b>\n"
        "<blockquote>"
        f"• Code: <code>{code}</code>\n"
        f"• User: {mention_user(int(user_id), full_name)}"
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


async def listpay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or not is_admin(update):
        return

    assert DB_POOL is not None
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch(
            """
            select code, user_id, full_name, username, created_at, done
            from pay_codes
            order by id desc
            """
        )

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

    def fmt_row(r):
        st = "DONE" if bool(r["done"]) else "PENDING"
        user_mention = mention_user(int(r["user_id"]), r["full_name"] or "")
        uname = f"@{norm_username(r['username'])}" if r["username"] else ""
        created = str(r["created_at"])
        return (
            f"• <code>{r['code']}</code> — {user_mention} {uname} — "
            f"<b>{st}</b> — {created}\n"
        )

    for r in rows:
        line = fmt_row(r)
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


# ✅ HTTP handlers
async def http_root(_request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def http_webhook_get(_request: web.Request) -> web.Response:
    # cho bạn mở /webhook bằng browser thấy OK (Telegram vẫn POST)
    return web.Response(text="OK")


def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ Missing BOT_TOKEN")
    if not DATABASE_URL:
        raise SystemExit("❌ Missing DATABASE_URL")

    # ✅ log để biết ENV có vào không
    print("PORT =", PORT)
    print("WEBHOOK_URL =", WEBHOOK_URL or "(empty)")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pay", pay))
    app.add_handler(CommandHandler("listpay", listpay))
    app.add_handler(CallbackQueryHandler(on_pay_done, pattern=r"^pay_done:\d+$"))

    if WEBHOOK_URL:
        webhook_path = "/webhook"
        full_webhook_url = WEBHOOK_URL.rstrip("/") + webhook_path

        # ✅ health routes
        app.webhook_app.router.add_get("/", http_root)
        app.webhook_app.router.add_get("/health", http_root)
        app.webhook_app.router.add_get("/webhook", http_webhook_get)

        print("✅ Bot is running (webhook)...", full_webhook_url)

        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=full_webhook_url,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
    else:
        print("✅ Bot is running (polling)...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

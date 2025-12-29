import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime
import html
import os

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

def safe(s: str) -> str:
    return html.escape(s or "")

def tag_user(user) -> str:
    if getattr(user, "username", None):
        return f"@{safe(user.username)}"
    name = safe(user.first_name or "Bạn")
    return f'<a href="tg://user?id={user.id}">{name}</a>'

def card(title: str, lines: list[str]) -> str:
    head = f"☰ <b>{safe(title)}</b>\n"
    sep = "──────────────"
    body = "\n".join(lines)
    return f"{head}{sep}\n{body}\n{sep}"

def fmt_user(u) -> str:
    full_name = (u.first_name or "") + (" " + u.last_name if u.last_name else "")
    username = u.username if u.username else "Không có"
    info = (
        "<blockquote>"
        f"• Name: <b>{safe(full_name.strip())}</b>\n"
        f"• Username: <b>@{safe(username)}</b>\n"
        f"• ID: <code>{u.id}</code>"
        "</blockquote>"
    )
    return info

def fmt_chat(chat) -> str:
    title = getattr(chat, "title", None) or (getattr(chat, "first_name", None) or "") + ((" " + chat.last_name) if getattr(chat, "last_name", None) else "")
    title = title.strip() or "Không có"
    username = f"@{chat.username}" if getattr(chat, "username", None) else "Không có"
    chat_type = getattr(chat, "type", "unknown")
    me = bot.get_me()
    bot_name = me.first_name
    info = (
        "<blockquote>"
        f"• Type: <b>{safe(chat.type)}</b>\n"
        f"• Name Chat: <b>{safe(title)}</b>\n"
        f"• Name Bot: <b>{safe(bot_name)}</b>\n"
        f"• Username: <b>{safe(username)}</b>\n"
        f"• ID Chanel Chat: <code>{chat.id}</code>"
        "</blockquote>"
    )
    return info

TOOL_URL = "https://thanhquycoder.id.vn/tool"
YT_URL = "https://youtube.com/@thanhquycoder"

def view_home(user) -> str:
    txt = (
        "<blockquote>"
        "• <b>Tải Tool</b> – Tải tool và hướng dẫn\n"
        "• <b>Youtube</b> – Xem kênh Youtube\n"
        "• <b>Admin</b> – Xem thông tin admin"
        "</blockquote>"
    )

    cmds = (
        "<blockquote>"
        "/tool\n"
        "/youtube\n"
        "/admin\n"
        "/user\n"
        "/id"
        "</blockquote>"
    )

    return card("Menu Bot", [
        f"Xin chào {tag_user(user)}! Bot đã sẵn sàng.",
        "",
        "- <b>Chức năng</b>:",
        txt,
        "",
        "- <b>Lệnh nhanh</b>:",
        cmds,
    ])

def view_tool() -> str:
    return card("Tải Tool", [
        f"<blockquote><b>Link tải</b>: {TOOL_URL}</blockquote>",
        "",
        "<blockquote>✍ <b>Lưu ý:</b> Nếu link lỗi vui lòng inbox <b>Admin</b> để được hỗ trợ nhanh nhất!</blockquote>"
    ])

def view_youtube() -> str:
    return card("Youtube", [
        f"<blockquote><b>Kênh Youtube</b>: {YT_URL}</blockquote>",
        "",
        "<blockquote>☞ <i>Ủng hộ mình 1 like và 1 subscribe nhé!</i></blockquote>",
    ])

def view_admin(chat_id: int) -> str:
    try:
        admin = bot.get_chat_member(chat_id, ADMIN_ID)
        u = admin.user

        full_name = (u.first_name or "") + (" " + u.last_name if u.last_name else "")
        username = "@" + u.username if u.username else "Không có"

        return card("Thông Tin Admin", [
            "<blockquote>",
            f"• Name: <b>{safe(full_name.strip())}</b>\n"
            f"• Username: <b>{safe(username)}</b>\n"
            f"• ID: <code>{u.id}</code>\n"
            f"• Quyền: <b>{safe(admin.status)}</b>"
            "</blockquote>",
        ])
    except:
        return card("Thông Tin Admin", [
            "<blockquote>! Không lấy được thông tin admin.</blockquote>"
        ])

def view_user(m) -> str:
    target = None
    if m.reply_to_message:
        target = m.reply_to_message.from_user
    elif len(m.text.split()) > 1:
        username = m.text.split()[1].lstrip("@").lower()
        try:
            members = bot.get_chat_administrators(m.chat.id)
            for mem in members:
                if mem.user.username and mem.user.username.lower() == username:
                    target = mem.user
                    break
        except:
            pass
    if not target:
        target = m.from_user
    time_str = datetime.fromtimestamp(m.date).strftime("%d/%m/%Y %H:%M:%S")
    full_name = (target.first_name or "") + (" " + target.last_name if target.last_name else "")
    username = "@" + target.username if target.username else "Không có"

    info = (
        "<blockquote>\n"
        f"• Name      : <b>{safe(full_name.strip())}</b>\n"
        f"• Username  : <b>{safe(username)}</b>\n"
        f"• ID        : <code>{target.id}</code>\n"
        f"• Thời gian : <b>{safe(time_str)}</b>\n"
        "</blockquote>"
    )

    hint = (
        "<blockquote>\n"
        "Gợi ý: Gõ /user để lấy thông tin người dùng theo reply hoặc username.\n"
        "Ví dụ: /user thanhquycoder"
        "</blockquote>"
    )
    return card("Thông Tin User", [info, "", hint])

def kb_home():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⇩ Tải Tool", callback_data="tool"),
        InlineKeyboardButton("🔥 Youtube", callback_data="youtube"),
    )
    kb.add(InlineKeyboardButton("✦ Admin", callback_data="admin"))
    return kb

def kb_back(extra: list[InlineKeyboardButton] | None = None):
    kb = InlineKeyboardMarkup(row_width=2)
    if extra:
        kb.add(*extra)
    kb.add(
        InlineKeyboardButton("⇦ Quay lại", callback_data="home"),
        InlineKeyboardButton("↻ Làm mới", callback_data="refresh"),
    )
    return kb

def kb_tool():
    return kb_back([
        InlineKeyboardButton("🌐 Mở Link Tool", url=TOOL_URL),
    ])

def kb_youtube():
    return kb_back([
        InlineKeyboardButton("🌐 Mở link Youtube", url=YT_URL),
    ])

def show(c_or_m, text: str, markup=None, edit=False):
    if edit:
        bot.edit_message_text(
            text,
            c_or_m.message.chat.id,
            c_or_m.message.message_id,
            reply_markup=markup
        )
    else:
        bot.send_message(c_or_m.chat.id, text, reply_markup=markup)

@bot.message_handler(commands=["start"])
def start(m):
    bot.send_message(m.chat.id, view_home(m.from_user), reply_markup=kb_home())

@bot.callback_query_handler(func=lambda c: True)
def cb(c):
    try:
        if c.data == "home":
            show(c, view_home(c.from_user), kb_home(), edit=True)

        elif c.data == "tool":
            show(c, view_tool(), kb_tool(), edit=True)

        elif c.data == "youtube":
            show(c, view_youtube(), kb_youtube(), edit=True)

        elif c.data == "admin":
            show(c, view_admin(c.message.chat.id), kb_back(), edit=True)

        elif c.data == "refresh":
            cur = c.message.text or ""
            bot.edit_message_text(
                cur,
                c.message.chat.id,
                c.message.message_id,
                reply_markup=c.message.reply_markup
            )
    except:
        bot.send_message(c.message.chat.id, view_home(c.from_user), reply_markup=kb_home())

    bot.answer_callback_query(c.id)

@bot.message_handler(commands=["tool"])
def cmd_tool(m):
    bot.send_message(m.chat.id, view_tool(), reply_markup=kb_tool())

@bot.message_handler(commands=["youtube"])
def cmd_youtube(m):
    bot.send_message(m.chat.id, view_youtube(), reply_markup=kb_youtube())

@bot.message_handler(commands=["admin"])
def cmd_admin(m):
    bot.send_message(m.chat.id, view_admin(m.chat.id), reply_markup=kb_back())

@bot.message_handler(commands=["user"])
def cmd_user(m):
    bot.send_message(m.chat.id, view_user(m), reply_markup=kb_home())

@bot.message_handler(commands=["id"])
def cmd_id(m):
    args = m.text.split(maxsplit=1)
    sub = args[1].strip().lower() if len(args) > 1 else ""
    if sub in ["channel", "kenh", "kênh"]:
        try:
            if m.chat.type == "channel":
                info = (
                    "<b>Channel hiện tại</b>: "
                    + fmt_chat(m.chat)
                )
                text = f"<blockquote>{info}</blockquote>"
                return bot.send_message(m.chat.id, text)

            fchat = getattr(m, "forward_from_chat", None)
            if fchat and getattr(fchat, "type", "") == "channel":
                info = (
                    "<b>Channel từ tin nhắn forward</b>: "
                    + fmt_chat(fchat)
                )
                text = f"<blockquote>{info}</blockquote>"
                return bot.send_message(m.chat.id, text)

            if m.reply_to_message:
                fchat2 = getattr(m.reply_to_message, "forward_from_chat", None)
                if fchat2 and getattr(fchat2, "type", "") == "channel":
                    info = (
                        "<b>Channel từ tin nhắn reply/forward</b>: "
                        + fmt_chat(fchat2)
                    )
                    text = f"<blockquote>{info}</blockquote>"
                    return bot.send_message(m.chat.id, text)
        except:
            pass

        info = (
            "<b>! Chưa lấy được ID kênh.</b>\n\n"
            "* <b>Hướng dẫn</b>:\n"
            "1) Thêm bot vào kênh (cấp quyền admin) rồi gõ: <code>/id channel</code> ngay trong kênh\n"
            "2) Hoặc forward 1 bài từ kênh vào bot, rồi gõ: <code>/id channel</code>"
        )
        text = f"<blockquote>{info}</blockquote>"
        return bot.send_message(m.chat.id, text)
    
    text = card("Thông Tin ID", [
        "- <b>Người Dùng</b>:",
        fmt_user(m.from_user),
        "",
        "- <b>Chanel Chat</b>:",
        fmt_chat(m.chat),
    ])
    bot.send_message(m.chat.id, text)
#############################################
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import os

def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, format, *args):
            return  # tắt log rác

    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

threading.Thread(target=start_health_server, daemon=True).start()

print("🤖 Bot đang chạy...")

bot.infinity_polling(skip_pending=True)


import os
import json
import base64
import logging
from datetime import datetime
import ccxt
from collections import defaultdict
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    Filters
)
from openai import OpenAI

# ======= Cấu hình =======
load_dotenv("KEY.env")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
YOUR_WALLET_ADDRESS = os.getenv("YOUR_WALLET_ADDRESS")

client = OpenAI(api_key=OPENAI_API_KEY)
logging.basicConfig(level=logging.INFO)
ALERTS_FILE = "alerts.json"
os.makedirs("images", exist_ok=True)

# Lưu cảnh báo đã lặp
alert_repeat_counter = defaultdict(lambda: defaultdict(int))

# ======= JSON Helper =======
def load_json(file):
    try:
        with open(file, "r") as f:
            return json.load(f)
    except:
        return {}

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=2)

# ======= GPT Phân tích ảnh =======
def encode_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

def analyze_chart_image(path):
    img_b64 = encode_image(path)
    filename = os.path.basename(path)
    coin_guess = filename.split("_")[0].upper() if "_" in filename else "Unknown"

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    "Bạn là chuyên gia phân tích kỹ thuật crypto. "
                    "Phân tích biểu đồ dưới đây và đoán tên coin nếu có thể. "
                    "Nhận định xu hướng, hỗ trợ/kháng cự và tín hiệu vào lệnh."
                )
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Phân tích biểu đồ (gợi ý coin: {coin_guess})"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                ]
            }
        ],
        max_tokens=1000
    )
    return response.choices[0].message.content

# ======= Funding Binance Futures =======
def fetch_funding_rate(symbol="BTC/USDT"):
    try:
        binance = ccxt.binance({'options': {'defaultType': 'future'}})
        binance.load_markets()
        funding = binance.fetch_funding_rate(symbol)
        return funding.get("fundingRate")
    except Exception as e:
        print(f"[ERROR] funding {symbol}: {e}")
        return None

def check_funding(context):
    alerts = load_json(ALERTS_FILE)
    bot = context.bot
    exchange = ccxt.binance({'options': {'defaultType': 'future'}})
    exchange.load_markets()

    for user_id, user_alerts in list(alerts.items()):
        for symbol, alert in list(user_alerts.items()):
            if not isinstance(alert, dict):
                continue

            try:
                operator = alert.get("operator", ">")
                threshold = float(alert.get("threshold", 0))
                funding_data = exchange.fetch_funding_rate(symbol)
                rate = funding_data.get("fundingRate") * 100  # chuyển về %

                if rate is None:
                    continue

                match = (
                    (operator == ">" and rate > threshold) or
                    (operator == ">=" and rate >= threshold) or
                    (operator == "<" and rate < threshold) or
                    (operator == "<=" and rate <= threshold) or
                    (operator == "=" and round(rate, 6) == round(threshold, 6))
                )

                if match:
                    count = alert_repeat_counter[user_id][symbol]
                    alert_repeat_counter[user_id][symbol] += 1

                    bot.send_message(
                        chat_id=user_id,
                        text=f"⚠️ Funding {symbol} = {rate:.3f}% {operator} {threshold}%"
                    )

                    if count >= 1:  # Nếu gửi lần 2
                        del alerts[user_id][symbol]
                        if not alerts[user_id]:
                            alerts.pop(user_id)
                        save_json(ALERTS_FILE, alerts)
                        alert_repeat_counter[user_id].pop(symbol, None)

                        bot.send_message(
                            chat_id=user_id,
                            text=f"✅ Đã xoá cảnh báo `{symbol}` sau 2 lần gửi.",
                            parse_mode="Markdown"
                        )
                else:
                    alert_repeat_counter[user_id][symbol] = 0  # reset nếu ko còn match
            except Exception as e:
                bot.send_message(chat_id=user_id, text=f"❌ Lỗi funding {symbol}: {e}")

# ======= Bot Commands =======
def start(update, context):
    msg = (
        "🤖 *Crypto GPT Bot miễn phí!*\n\n"
        "• 📷 Gửi ảnh biểu đồ để GPT phân tích\n"
        "• 📈 Cảnh báo funding: `/setfunding BTC > -0.01`\n"
        "• 📋 Xem cảnh báo: `/funding`\n"
        "• 💖 Ủng hộ: `/donate`"
    )
    update.message.reply_text(msg, parse_mode="Markdown")

def analyze_instruction(update, context):
    update.message.reply_text("📷 Gửi ảnh biểu đồ bạn muốn GPT phân tích.")

def donate(update, context):
    msg = (
        "🙏 *Ủng hộ phát triển bot*\n\n"
        f"Ví USDT (BEP20):\n`{YOUR_WALLET_ADDRESS}`\n\n"
        "Cảm ơn bạn rất nhiều! ❤️"
    )
    update.message.reply_text(msg, parse_mode="Markdown")

def handle_photo(update, context):
    photo = update.message.photo[-1]
    file = photo.get_file()
    path = f"images/{photo.file_id}.jpg"
    file.download(path)
    update.message.reply_text("🧠 GPT đang phân tích biểu đồ...")
    try:
        result = analyze_chart_image(path)
        update.message.reply_text(result)
    except Exception as e:
        update.message.reply_text(f"❌ Lỗi GPT: {e}")
    finally:
        os.remove(path)

def set_funding(update, context):
    user_id = str(update.effective_user.id)
    try:
        if len(context.args) < 3:
            raise ValueError("Thiếu cú pháp. Dạng đúng: /setfunding BTC > -0.01")

        symbol = context.args[0].upper() + "/USDT"
        operator = context.args[1]
        threshold = float(context.args[2])

        if operator not in [">", ">=", "<", "<=", "="]:
            raise ValueError("Toán tử không hợp lệ. Dùng >, >=, <, <=, =")

        alerts = load_json(ALERTS_FILE)
        user_alerts = alerts.get(user_id, {})

        if len(user_alerts) >= 8 and symbol not in user_alerts:
            update.message.reply_text("⚠️ Tối đa 8 coin được theo dõi.")
            return

        user_alerts[symbol] = {
            "symbol": symbol,
            "threshold": threshold,
            "operator": operator
        }
        alerts[user_id] = user_alerts
        save_json(ALERTS_FILE, alerts)

        update.message.reply_text(
            f"✅ Đã đặt cảnh báo funding: {symbol} {operator} {threshold}",
            parse_mode="Markdown"
        )
    except Exception as e:
        update.message.reply_text(f"❌ Lỗi: {e}\n📌 Dạng đúng: /setfunding BTC > -0.01", parse_mode="Markdown")

def funding_menu(update, context):
    user_id = str(update.effective_user.id)
    alerts = load_json(ALERTS_FILE)
    user_alerts = alerts.get(user_id, {})

    if not user_alerts:
        update.message.reply_text("📭 Chưa có cảnh báo nào.")
        return

    lines = ["📊 Danh sách cảnh báo:"]
    for symbol, alert in user_alerts.items():
        lines.append(f"• `{symbol}` {alert['operator']} `{alert['threshold']}`")
    keyboard = [[InlineKeyboardButton("❌ Xoá tất cả", callback_data="delete_all")]]
    update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def handle_callback(update, context):
    query = update.callback_query
    user_id = str(query.from_user.id)
    alerts = load_json(ALERTS_FILE)

    if query.data == "delete_all":
        alerts.pop(user_id, None)
        save_json(ALERTS_FILE, alerts)
        query.edit_message_text("✅ Đã xoá toàn bộ cảnh báo.")

# ======= Main =======
def main():
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("analyze", analyze_instruction))
    dp.add_handler(CommandHandler("donate", donate))
    dp.add_handler(CommandHandler("setfunding", set_funding))
    dp.add_handler(CommandHandler("funding", funding_menu))
    dp.add_handler(CallbackQueryHandler(handle_callback))
    dp.add_handler(MessageHandler(Filters.photo, handle_photo))

    updater.job_queue.run_repeating(check_funding, interval=300, first=5)

    updater.bot.set_my_commands([
        BotCommand("start", "Giới thiệu bot"),
        BotCommand("analyze", "Phân tích ảnh biểu đồ"),
        BotCommand("setfunding", "Đặt cảnh báo funding"),
        BotCommand("funding", "Xem hoặc xóa cảnh báo"),
        BotCommand("donate", "Ủng hộ bot")
    ])

    updater.start_polling()
    print("🤖 Bot đang chạy...")
    updater.idle()

if __name__ == "__main__":
    main()

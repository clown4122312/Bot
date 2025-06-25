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
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from openai import OpenAI

# ======= Cấu hình =======
load_dotenv("KEY.env")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
YOUR_WALLET_ADDRESS = os.getenv("YOUR_WALLET_ADDRESS")
PORT = int(os.environ.get("PORT", 8443))
RENDER_DOMAIN = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "your_render_service_name.onrender.com")

client = OpenAI(api_key=OPENAI_API_KEY)
logging.basicConfig(level=logging.INFO)
ALERTS_FILE = "alerts.json"
os.makedirs("images", exist_ok=True)

alert_repeat_counter = defaultdict(lambda: defaultdict(int))

PROXY = {
    'http': 'http://v2-506-403548:OWUVP@14.161.29.217:15506',
    'https': 'http://v2-506-403548:OWUVP@14.161.29.217:15506'
}

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
            {"role": "system", "content": "Bạn là chuyên gia phân tích kỹ thuật crypto..."},
            {"role": "user", "content": [
                {"type": "text", "text": f"Phân tích biểu đồ (gợi ý coin: {coin_guess})"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
            ]}
        ],
        max_tokens=1000
    )
    return response.choices[0].message.content

# ======= Funding Binance Futures =======
def fetch_funding_rate(symbol="BTC/USDT"):
    try:
        binance = ccxt.binance({
            'options': {'defaultType': 'future'},
            'proxies': PROXY
        })
        binance.load_markets()
        funding = binance.fetch_funding_rate(symbol)
        return funding.get("fundingRate")
    except Exception as e:
        print(f"[ERROR] funding {symbol}: {e}")
        return None

def check_funding(context: ContextTypes.DEFAULT_TYPE):
    alerts = load_json(ALERTS_FILE)
    bot = context.bot
    exchange = ccxt.binance({
        'options': {'defaultType': 'future'},
        'proxies': PROXY
    })
    exchange.load_markets()

    for user_id, user_alerts in list(alerts.items()):
        for symbol, alert in list(user_alerts.items()):
            if not isinstance(alert, dict):
                continue
            try:
                operator = alert.get("operator", ">")
                threshold = float(alert.get("threshold", 0))
                funding_data = exchange.fetch_funding_rate(symbol)
                rate = funding_data.get("fundingRate") * 100

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

                    bot.send_message(chat_id=user_id, text=f"⚠️ Funding {symbol} = {rate:.3f}% {operator} {threshold}%")

                    if count >= 1:
                        del alerts[user_id][symbol]
                        if not alerts[user_id]:
                            alerts.pop(user_id)
                        save_json(ALERTS_FILE, alerts)
                        alert_repeat_counter[user_id].pop(symbol, None)

                        bot.send_message(chat_id=user_id, text=f"✅ Đã xoá cảnh báo `{symbol}` sau 2 lần gửi.", parse_mode="Markdown")
                else:
                    alert_repeat_counter[user_id][symbol] = 0
            except Exception as e:
                bot.send_message(chat_id=user_id, text=f"❌ Lỗi funding {symbol}: {e}")

# Các hàm xử lý lệnh (start, donate, setfunding, v.v.) giữ nguyên như cũ

# ======= Main dùng Webhook =======
async def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analyze", analyze_instruction))
    app.add_handler(CommandHandler("donate", donate))
    app.add_handler(CommandHandler("setfunding", set_funding))
    app.add_handler(CommandHandler("funding", funding_menu))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    app.job_queue.run_repeating(check_funding, interval=300, first=5)

    await app.bot.set_my_commands([
        BotCommand("start", "Giới thiệu bot"),
        BotCommand("analyze", "Phân tích ảnh biểu đồ"),
        BotCommand("setfunding", "Đặt cảnh báo funding"),
        BotCommand("funding", "Xem hoặc xóa cảnh báo"),
        BotCommand("donate", "Ủng hộ bot")
    ])

    await app.start()
    await app.updater.start_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_BOT_TOKEN,
        webhook_url=f"https://{RENDER_DOMAIN}/{TELEGRAM_BOT_TOKEN}"
    )
    print("🤖 Bot đang chạy với webhook...")
    await app.updater.idle()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

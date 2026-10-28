import os
import requests

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BINANCE_URL = "https://data-api.binance.vision"

def main():
    if not TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Не заданы TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID в Secrets."
        )

    # Проверяем получение рыночных данных Binance.
    response = requests.get(
        f"{BINANCE_URL}/api/v3/ticker/price",
        params={"symbol": "BTCUSDT"},
        timeout=20,
    )
    response.raise_for_status()
    btc_price = response.json()["price"]

    # Проверяем отправку сообщения в Telegram.
    chart_url = "https://www.binance.com/en/futures/BTCUSDT"

    message = (
        "✅ ПРОВЕРКА БОТА\n\n"
        f"BTCUSDT: {btc_price}\n"
        f'🔗 <a href="{chart_url}">ОТКРЫТЬ BINANCE FUTURES</a>\n\n'
        "Binance и Telegram доступны."
    )

    tg_response = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    tg_response.raise_for_status()

    result = tg_response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Ошибка Telegram: {result}")

    print("Проверка успешна.")

if __name__ == "__main__":
    main()

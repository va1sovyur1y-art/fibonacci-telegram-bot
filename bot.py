import os
import time
import requests
from datetime import datetime, timezone

BASE_URL = "https://data-api.binance.vision"
TIMEFRAME = "15m"
TOP_N = 20
ATR_PERIOD = 14
MIN_IMPULSE_ATR = 2.0
MIN_EFFICIENCY = 0.55

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

EXCLUDED = {
    "USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE",
    "EUR", "EURC", "XAUT"
}
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})


def get_json(path, params=None):
    response = session.get(
        BASE_URL + path, params=params, timeout=20
    )
    response.raise_for_status()
    return response.json()


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    response = session.post(
        url,
        data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=20
    )
    response.raise_for_status()


def get_symbols():
    info = get_json("/api/v3/exchangeInfo")
    tickers = get_json("/api/v3/ticker/24hr")
    valid = {
        item["symbol"]
        for item in info["symbols"]
        if item["status"] == "TRADING"
        and item["quoteAsset"] == "USDT"
        and item["isSpotTradingAllowed"]
    }

    excluded_suffixes = (
        "UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"
    )
    ranked = sorted(
        [
            t for t in tickers
            if t["symbol"] in valid
            and not t["symbol"].endswith(excluded_suffixes)
            and t["symbol"][:-4] not in EXCLUDED
        ],
        key=lambda t: float(t["quoteVolume"]),
        reverse=True
    )
    return [t["symbol"] for t in ranked[:TOP_N]]


def get_candles(symbol):
    data = get_json(
        "/api/v3/klines",
        {"symbol": symbol, "interval": TIMEFRAME, "limit": 40}
    )
    # Последняя свеча ещё формируется — не используем её.
    return [
        {
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "time": int(c[0])
        }
        for c in data[:-1]
    ]


def find_setup(symbol):
    candles = get_candles(symbol)
    if len(candles) < ATR_PERIOD + 5:
        return None

    trs = []
    for i in range(1, len(candles)):
        c, prev = candles[i], candles[i - 1]
        trs.append(max(
            c["high"] - c["low"],
            abs(c["high"] - prev["close"]),
            abs(c["low"] - prev["close"])
        ))

    atr = sum(trs[-ATR_PERIOD:]) / ATR_PERIOD
    if atr <= 0:
        return None

    candidates = []
    for length in (3, 4, 5):
        end = len(candles) - 1
        start = end - length + 1
        part = candles[start:end + 1]
        low = min(c["low"] for c in part)
        high = max(c["high"] for c in part)
        move = high - low

        if move < MIN_IMPULSE_ATR * atr or low <= 0:
            continue

        first = part[0]["open"]
        last = part[-1]["close"]
        direction = "LONG" if last > first else "SHORT"
        if last == first:
            continue

        body = abs(last - first)
        efficiency = body / move if move else 0
        if efficiency < MIN_EFFICIENCY:
            continue

        if direction == "LONG":
            entry = high - move * 0.618
            stop = high - move * 0.786
            risk = entry - stop
            tp3 = entry + 3 * risk
            tp4 = entry + 4 * risk
        else:
            entry = low + move * 0.618
            stop = low + move * 0.786
            risk = stop - entry
            tp3 = entry - 3 * risk
            tp4 = entry - 4 * risk

        current = candles[-1]["close"]
        if direction == "LONG" and current <= stop:
            continue
        if direction == "SHORT" and current >= stop:
            continue

        candidates.append({
            "symbol": symbol,
            "direction": direction,
            "move_pct": move / low * 100,
            "entry": entry,
            "stop": stop,
            "tp3": tp3,
            "tp4": tp4,
            "current": current,
            "time": part[-1]["time"],
            "length": length
        })

    if not candidates:
        return None

    return max(candidates, key=lambda x: x["move_pct"])


def format_signal(s):
    price = lambda x: f"{x:.8f}".rstrip("0").rstrip(".")

    direction = (
        "LONG 🟢"
        if s["direction"] == "LONG"
        else "SHORT 🔴"
    )

    symbol = s["symbol"]
    pair = symbol.removesuffix("USDT") + "_USDT"

    chart_url = f"https://www.binance.com/en/futures/{pair}"

    return (
        "📊 НОВЫЙ СЕТАП — ПРОВЕРЬ НА ГРАФИКЕ\n\n"
        f"Монета: <a href=\"{chart_url}\">{symbol}</a>\n"
        f"Направление: {direction}\n"
        f"Импульс: {s['move_pct']:.3f}% ({s['length']} свечи)\n"
        f"Вход 0.618: {price(s['entry'])}\n"
        f"Стоп 0.786: {price(s['stop'])}\n"
        f"TP 1:3: {price(s['tp3'])}\n"
        f"TP 1:4: {price(s['tp4'])}\n\n"
        "Это кандидат на сетап, не подтверждённый вход."
    )


def main():
    print("Запуск сканера:", datetime.now(timezone.utc).isoformat())
    symbols = get_symbols()
    print("Монет для проверки:", len(symbols))

    for symbol in symbols:
        try:
            setup = find_setup(symbol)
            if setup:
                key = (
                    f"{setup['symbol']}_{setup['direction']}_"
                    f"{setup['time']}_{setup['length']}"
                )
                # Не отправляем один и тот же сетап повторно.
                state = set()
                if os.path.exists("sent_signals.txt"):
                    with open("sent_signals.txt", encoding="utf-8") as f:
                        state = set(f.read().splitlines())

                if key not in state:
                    send_telegram(format_signal(setup))
                    with open("sent_signals.txt", "a", encoding="utf-8") as f:
                        f.write(key + "\n")
                    print("Отправлен:", symbol)
            time.sleep(0.15)
        except Exception as e:
            print(f"Ошибка {symbol}: {e}")

    print("Сканирование завершено.")


if __name__ == "__main__":
    main()

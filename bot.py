import os
import requests

BASE_URL = "https://data-api.binance.vision"

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TIMEFRAME = "15m"
ATR_PERIOD = 14
MIN_IMPULSE_ATR = 2.0
MIN_EFFICIENCY = 0.55
IMPULSE_CANDLES = (3, 4, 5)
TOP_SYMBOLS = 20

# Стейблкоины, которые не должны быть базовой монетой.
STABLE_BASES = {
    "USDT", "USDC", "USD1", "FDUSD", "TUSD",
    "DAI", "USDE", "USDS", "PYUSD", "USDP",
    "EURC", "USTC", "BUSD"
}

# Исключаем токены с плечом UP/DOWN/BULL/BEAR.
LEVERAGED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

session = requests.Session()
session.headers.update({
    "User-Agent": "FibonacciTelegramScanner/1.0"
})


def get_json(url, params=None):
    response = session.get(
        url,
        params=params,
        timeout=20
    )
    response.raise_for_status()
    return response.json()


def get_top_symbols():
    data = get_json(f"{BASE_URL}/api/v3/ticker/24hr")
    symbols = []

    for item in data:
        symbol = item.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue

        base = symbol[:-4]

        if base in STABLE_BASES:
            continue

        if any(symbol.endswith(x) for x in LEVERAGED_SUFFIXES):
            continue

        try:
            volume = float(item["quoteVolume"])
        except (ValueError, TypeError, KeyError):
            continue

        symbols.append((symbol, volume))

    symbols.sort(key=lambda x: x[1], reverse=True)

    return [symbol for symbol, _ in symbols[:TOP_SYMBOLS]]


def get_klines(symbol):
    data = get_json(
        f"{BASE_URL}/api/v3/klines",
        {
            "symbol": symbol,
            "interval": TIMEFRAME,
            "limit": 100,
        },
    )

    # Не используем последнюю незакрытую свечу.
    return data[:-1]


def calculate_atr(candles, period=ATR_PERIOD):
    ranges = []

    for i in range(1, len(candles)):
        high = float(candles[i][2])
        low = float(candles[i][3])
        previous_close = float(candles[i - 1][4])

        ranges.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )

    if len(ranges) < period:
        return None

    return sum(ranges[-period:]) / period


def find_impulses(symbol):
    candles = get_klines(symbol)

    if len(candles) < 30:
        return []

    atr = calculate_atr(candles)

    if not atr or atr <= 0:
        return []

    setups = []

    for length in IMPULSE_CANDLES:
        recent = candles[-length:]

        start = float(recent[0][1])
        end = float(recent[-1][4])

        if start <= 0:
            continue

        move = end - start
        move_abs = abs(move)

        if move_abs < atr * MIN_IMPULSE_ATR:
            continue

        close_changes = [
            abs(float(recent[i][4]) - float(recent[i - 1][4]))
            for i in range(1, len(recent))
        ]

        path = sum(close_changes) + abs(
            float(recent[0][4]) - float(recent[0][1])
        )

        efficiency = move_abs / path if path > 0 else 0

        if efficiency < MIN_EFFICIENCY:
            continue

        high = max(float(c[2]) for c in recent)
        low = min(float(c[3]) for c in recent)
        span = high - low

        if span <= 0:
            continue

        if move > 0:
            direction = "LONG"
            entry = high - span * 0.618
            stop = high - span * 0.786
            risk = entry - stop

            tp3 = entry + 3 * risk
            tp4 = entry + 4 * risk

        else:
            direction = "SHORT"
            entry = low + span * 0.618
            stop = low + span * 0.786
            risk = stop - entry

            tp3 = entry - 3 * risk
            tp4 = entry - 4 * risk

        if risk <= 0:
            continue

        setups.append({
            "symbol": symbol,
            "direction": direction,
            "move_pct": move_abs / start * 100,
            "length": length,
            "entry": entry,
            "stop": stop,
            "tp3": tp3,
            "tp4": tp4,
            "candle_time": int(recent[-1][0]),
        })

    if not setups:
        return []

    # Один наиболее сильный импульс на монету.
    setups.sort(
        key=lambda s: s["move_pct"],
        reverse=True
    )

    return [setups[0]]


def format_price(value):
    return f"{value:.8f}".rstrip("0").rstrip(".")


def format_signal(s):
    symbol = s["symbol"]

    direction = (
        "LONG 🟢"
        if s["direction"] == "LONG"
        else "SHORT 🔴"
    )

    chart_url = (
        f"https://www.tradingview.com/chart/"
        f"?symbol=BINANCE:{symbol}"
    )

    futures_url = (
        f"https://www.binance.com/en/futures/{symbol}"
    )

    message = (
        "📊 НОВЫЙ СЕТАП — ПРОВЕРЬ НА ГРАФИКЕ\n\n"
        f"Монета: {symbol}\n"
        f"Направление: {direction}\n"
        f"Импульс: {s['move_pct']:.3f}% "
        f"({s['length']} свечи)\n\n"
        f"Вход 0.618: {format_price(s['entry'])}\n"
        f"Стоп 0.786: {format_price(s['stop'])}\n"
        f"TP 1:3: {format_price(s['tp3'])}\n"
        f"TP 1:4: {format_price(s['tp4'])}\n\n"
        "⚠️ Кандидат на сетап, не подтверждённый вход.\n"
        "Проверь график перед принятием решения."
    )

    keyboard = {
        "inline_keyboard": [
            [
                {
                    "text": "📊 Рассчитать маржу",
                    "url": futures_url
                }
            ],
            [
                {
                    "text": "📈 Открыть график",
                    "url": chart_url
                }
            ]
        ]
    }

    return message, keyboard


def send_telegram(message, keyboard):
    response = session.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={
            "chat_id": CHAT_ID,
            "text": message,
            "reply_markup": keyboard,
            "disable_web_page_preview": True,
        },
        timeout=20,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {result}"
        )


def load_sent():
    try:
        with open("sent_signals.txt", "r", encoding="utf-8") as f:
            return set(
                line.strip()
                for line in f
                if line.strip()
            )
    except FileNotFoundError:
        return set()


def save_sent(sent):
    with open("sent_signals.txt", "w", encoding="utf-8") as f:
        for item in sorted(sent):
            f.write(item + "\n")


def main():
    if not TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Проверь GitHub Secrets: "
            "TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID."
        )

    print("Запуск Fibonacci Telegram Scanner")

    sent = load_sent()
    symbols = get_top_symbols()

    print(
        f"Сканируем {len(symbols)} пар "
        f"на таймфрейме {TIMEFRAME}"
    )

    for symbol in symbols:
        try:
            setups = find_impulses(symbol)

            for s in setups:
                signal_id = (
                    f"{s['symbol']}:"
                    f"{s['direction']}:"
                    f"{s['candle_time']}"
                )

                if signal_id in sent:
                    print(
                        f"Уже отправлен: "
                        f"{symbol} {s['direction']}"
                    )
                    continue

                message, keyboard = format_signal(s)

                send_telegram(message, keyboard)

                sent.add(signal_id)
                save_sent(sent)

                print(
                    f"Отправлен сетап: "
                    f"{symbol} {s['direction']}"
                )

        except Exception as error:
            print(f"Ошибка для {symbol}: {error}")

    print("Сканирование завершено.")


if __name__ == "__main__":
    main()bounce_alerts += 1
                        break
                s["last_checked_candle"] = ctime
        except Exception as exc:
            log.warning("Не удалось проверить %s: %s", s.get("symbol"), exc)

    for symbol in symbols:
        try:
            df = get_closed_candles(symbol, limit=100)
            if df.empty:
                continue
            for impulse in find_impulses(df):
                s = make_setup(symbol, impulse)
                if s is None:
                    continue
                key = setup_key(s)
                if key in state["setups"]:
                    continue
                state["setups"][key] = s
                send_telegram(setup_message(s))
                new_setups += 1
        except Exception as exc:
            log.warning("Ошибка сканирования %s: %s", symbol, exc)

    save_state(state)
    log.info("Завершено. Новых сетапов: %d; уведомлений об отбое: %d", new_setups, bounce_alerts)


if __name__ == "__main__":
    run_once()

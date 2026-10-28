import os
import requests

BASE_URL = "https://data-api.binance.vision"
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

TIMEFRAME = "15m"
ATR_PERIOD = 14
MIN_IMPULSE_ATR = 2.0
MIN_EFFICIENCY = 0.55
IMPULSE_CANDLES = [3, 4, 5]
TOP_SYMBOLS = 20

session = requests.Session()


def get_top_symbols():
    url = f"{BASE_URL}/api/v3/ticker/24hr"
    response = session.get(url, timeout=20)
    response.raise_for_status()

    data = response.json()
    symbols = []

    excluded = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

    for item in data:
        symbol = item.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue
        if any(symbol.endswith(x) for x in excluded):
            continue

        try:
            volume = float(item["quoteVolume"])
        except (KeyError, ValueError, TypeError):
            continue

        symbols.append((symbol, volume))

    symbols.sort(key=lambda x: x[1], reverse=True)
    return [symbol for symbol, _ in symbols[:TOP_SYMBOLS]]


def get_klines(symbol):
    url = f"{BASE_URL}/api/v3/klines"
    response = session.get(
        url,
        params={
            "symbol": symbol,
            "interval": TIMEFRAME,
            "limit": 100,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def calculate_atr(candles, period=ATR_PERIOD):
    true_ranges = []

    for i in range(1, len(candles)):
        high = float(candles[i][2])
        low = float(candles[i][3])
        previous_close = float(candles[i - 1][4])

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )
        true_ranges.append(true_range)

    if len(true_ranges) < period:
        return None

    return sum(true_ranges[-period:]) / period


def find_impulses(symbol):
    try:
        candles = get_klines(symbol)
        if len(candles) < 30:
            return []

        # Не используем последнюю, ещё формирующуюся свечу.
        candles = candles[:-1]
        atr = calculate_atr(candles)

        if not atr or atr <= 0:
            return []

        setups = []

        # Ищем импульс на последних 3, 4 и 5 закрытых свечах.
        for length in IMPULSE_CANDLES:
            recent = candles[-length:]

            start_price = float(recent[0][1])
            end_price = float(recent[-1][4])

            if start_price <= 0:
                continue

            move = end_price - start_price
            move_abs = abs(move)
            move_pct = move_abs / start_price * 100

            if move_abs < atr * MIN_IMPULSE_ATR:
                continue

            # Фильтр эффективности движения.
            total_range = sum(
                abs(float(c[4]) - float(c[1])) for c in recent
            )
            efficiency = (
                move_abs / total_range if total_range > 0 else 0
            )

            if efficiency < MIN_EFFICIENCY:
                continue

            high = max(float(c[2]) for c in recent)
            low = min(float(c[3]) for c in recent)

            if high <= low:
                continue

            if move > 0:
                direction = "LONG"
                entry = high - (high - low) * 0.618
                stop = high - (high - low) * 0.786
                risk = entry - stop

                if risk <= 0:
                    continue

                tp3 = entry + risk * 3
                tp4 = entry + risk * 4

            else:
                direction = "SHORT"
                entry = low + (high - low) * 0.618
                stop = low + (high - low) * 0.786
                risk = stop - entry

                if risk <= 0:
                    continue

                tp3 = entry - risk * 3
                tp4 = entry - risk * 4

            setups.append({
                "symbol": symbol,
                "direction": direction,
                "move_pct": move_pct,
                "length": length,
                "entry": entry,
                "stop": stop,
                "tp3": tp3,
                "tp4": tp4,
                "impulse_end": int(recent[-1][0]),
            })

        # Для одной монеты оставляем самый сильный подходящий импульс.
        if not setups:
            return []

        setups.sort(key=lambda x: x["move_pct"], reverse=True)
        return [setups[0]]

    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        print(f"Ошибка обработки {symbol}: {e}")
        return []


def format_price(value):
    return f"{value:.8f}".rstrip("0").rstrip(".")


def format_signal(s):
    symbol = s["symbol"]

    # Ссылка отдельной строкой — Telegram сможет сделать её кликабельной.
    chart_url = f"https://www.binance.com/en/futures/{symbol}"

    if s["direction"] == "LONG":
        direction = "LONG 🟢"
    else:
        direction = "SHORT 🔴"

    return (
        "📊 НОВЫЙ СЕТАП — ПРОВЕРЬ НА ГРАФИКЕ\n\n"
        f"Монета: {symbol}\n"
        f"🔗 <a href=\"{chart_url}\">ОТКРЫТЬ ГРАФИК BINANCE FUTURES</a>\n\n"
        f"Направление: {direction}\n"
        f"Импульс: {s['move_pct']:.3f}% ({s['length']} свечи)\n"
        f"Вход 0.618: {format_price(s['entry'])}\n"
        f"Стоп 0.786: {format_price(s['stop'])}\n"
        f"TP 1:3: {format_price(s['tp3'])}\n"
        f"TP 1:4: {format_price(s['tp4'])}\n\n"
        "Это кандидат на сетап, не подтверждённый вход."
    )


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    response = session.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )

    response.raise_for_status()
    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")


def load_sent_signals():
    try:
        with open("sent_signals.txt", "r", encoding="utf-8") as file:
            return set(line.strip() for line in file if line.strip())
    except FileNotFoundError:
        return set()


def save_sent_signals(sent):
    with open("sent_signals.txt", "w", encoding="utf-8") as file:
        for item in sorted(sent):
            file.write(item + "\n")


def main():
    sent = load_sent_signals()
    symbols = get_top_symbols()

    print(f"Проверяем монет: {len(symbols)}")

    new_count = 0

    for symbol in symbols:
        setups = find_impulses(symbol)

        for s in setups:
            signal_id = (
                f"{s['symbol']}:{s['direction']}:{s['impulse_end']}"
            )

            if signal_id in sent:
                continue

            message = format_signal(s)
            send_telegram(message)

            sent.add(signal_id)
            save_sent_signals(sent)
            new_count += 1

            print(f"Отправлен сетап: {symbol} {s['direction']}")

    print(f"Готово. Новых сообщений: {new_count}")


if __name__ == "__main__":
    main()

import json
import os
import requests

BASE_URL = "https://data-api.binance.vision"
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TIMEFRAME = "15m"
ATR_PERIOD = 14
MIN_IMPULSE_ATR = 2.0
MIN_EFFICIENCY = 0.55
IMPULSE_MIN_CANDLES = 3
IMPULSE_MAX_CANDLES = 12
TOP_SYMBOLS = 20
ACTIVE_FILE = "active_setups.json"
SENT_FILE = "sent_signals.txt"

STABLE_BASES = {
    "USDT", "USDC", "USD1", "FDUSD", "TUSD", "DAI", "USDE",
    "USDS", "PYUSD", "USDP", "EURC", "USTC", "BUSD"
}
LEVERAGED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

session = requests.Session()
session.headers.update({"User-Agent": "FibonacciTelegramScanner/1.0"})


def get_json(url, params=None):
    response = session.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def get_top_symbols():
    data = get_json(f"{BASE_URL}/api/v3/ticker/24hr")
    symbols = []
    for item in data:
        symbol = item.get("symbol", "")
        if not symbol.endswith("USDT") or symbol[:-4] in STABLE_BASES:
            continue
        if any(symbol.endswith(suffix) for suffix in LEVERAGED_SUFFIXES):
            continue
        try:
            volume = float(item["quoteVolume"])
        except (ValueError, TypeError, KeyError):
            continue
        symbols.append((symbol, volume))
    symbols.sort(key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ in symbols[:TOP_SYMBOLS]]


def get_klines(symbol):
    data = get_json(
        f"{BASE_URL}/api/v3/klines",
        {"symbol": symbol, "interval": TIMEFRAME, "limit": 100},
    )
    # Исключаем последнюю, ещё формирующуюся свечу.
    return data[:-1]


def get_price(symbol):
    data = get_json(f"{BASE_URL}/api/v3/ticker/price", {"symbol": symbol})
    return float(data["price"])


def calculate_atr(candles, period=ATR_PERIOD):
    ranges = []
    for i in range(1, len(candles)):
        high = float(candles[i][2])
        low = float(candles[i][3])
        previous_close = float(candles[i - 1][4])
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    if len(ranges) < period:
        return None
    return sum(ranges[-period:]) / period


def detect_impulse(symbol):
    candles = get_klines(symbol)
    if len(candles) < 40:
        return None
    atr = calculate_atr(candles)
    if not atr or atr <= 0:
        return None

    candidates = []
    # Рассматриваем только полностью закрытые окна длиной 3–12 свечей,
    # заканчивающиеся на последней закрытой свече.
    end_index = len(candles)
    for length in range(IMPULSE_MIN_CANDLES, IMPULSE_MAX_CANDLES + 1):
        recent = candles[end_index - length:end_index]
        start = float(recent[0][1])
        end = float(recent[-1][4])
        if start <= 0:
            continue

        move = end - start
        move_abs = abs(move)
        if move_abs < atr * MIN_IMPULSE_ATR:
            continue

        closes = [float(candle[4]) for candle in recent]
        path = abs(closes[0] - float(recent[0][1])) + sum(
            abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))
        )
        efficiency = move_abs / path if path > 0 else 0
        if efficiency < MIN_EFFICIENCY:
            continue

        # Отсекаем окна, в которых слишком мало движения в сторону импульса.
        previous = float(recent[0][1])
        directional_steps = 0
        for candle in recent:
            close = float(candle[4])
            if (close - previous) * move > 0:
                directional_steps += 1
            previous = close
        if directional_steps < max(2, int(length * 0.55)):
            continue

        high = max(float(candle[2]) for candle in recent)
        low = min(float(candle[3]) for candle in recent)
        span = high - low
        if span <= 0:
            continue

        if move > 0:
            direction = "LONG"
            alert_level = high - span * 0.5
            entry = high - span * 0.618
            stop = high - span * 0.786
            risk = entry - stop
            tp3, tp4 = entry + 3 * risk, entry + 4 * risk
        else:
            direction = "SHORT"
            alert_level = low + span * 0.5
            entry = low + span * 0.618
            stop = low + span * 0.786
            risk = stop - entry
            tp3, tp4 = entry - 3 * risk, entry - 4 * risk

        if risk <= 0:
            continue

        candidates.append({
            "symbol": symbol,
            "direction": direction,
            "move_pct": move_abs / start * 100,
            "length": length,
            "high": high,
            "low": low,
            "entry": entry,
            "alert_level": alert_level,
            "stop": stop,
            "tp3": tp3,
            "tp4": tp4,
            "candle_time": int(recent[-1][0]),
            "status": "waiting",
        })

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item["move_pct"], item["length"]), reverse=True)
    return candidates[0]


def format_price(value):
    return f"{value:.8f}".rstrip("0").rstrip(".")


def format_alert(setup, price, entry_already_passed):
    symbol = setup["symbol"]
    direction = "LONG 🟢" if setup["direction"] == "LONG" else "SHORT 🔴"
    chart_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}"
    futures_url = f"https://www.binance.com/en/futures/{symbol}"
    entry_note = (
        "⚠️ Цена уже прошла уровень 0.618 — проверь график, не ставь ордер вслепую."
        if entry_already_passed else
        "Лимитный уровень 0.618 ещё не пройден по текущей цене."
    )
    message = (
        "🚨 ЦЕНА ДОСТИГЛА ФИБОНАЧЧИ 0.5\n\n"
        f"Монета: {symbol}\n"
        f"Направление: {direction}\n"
        f"Импульс: {setup['move_pct']:.3f}% ({setup['length']} свечей M15)\n"
        f"Текущая цена: {format_price(price)}\n\n"
        f"🔔 Уровень 0.5: {format_price(setup['alert_level'])}\n"
        f"🎯 Лимитный вход 0.618: {format_price(setup['entry'])}\n"
        f"🛑 Стоп 0.786: {format_price(setup['stop'])}\n"
        f"✅ TP 1:3: {format_price(setup['tp3'])}\n"
        f"🚀 TP 1:4: {format_price(setup['tp4'])}\n\n"
        f"{entry_note}\n"
        "⚠️ Уведомление для проверки графика, не торговая рекомендация."
    )
    keyboard = {"inline_keyboard": [
        [{"text": "📊 Binance Futures", "url": futures_url}],
        [{"text": "📈 Открыть график", "url": chart_url}],
    ]}
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
        raise RuntimeError(f"Telegram API error: {result}")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_sent():
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as file:
            return {line.strip() for line in file if line.strip()}
    except FileNotFoundError:
        return set()


def save_sent(sent):
    with open(SENT_FILE, "w", encoding="utf-8") as file:
        for item in sorted(sent):
            file.write(item + "\n")


def stop_reached(setup, price):
    return price <= setup["stop"] if setup["direction"] == "LONG" else price >= setup["stop"]


def alert_reached(setup, price):
    return price <= setup["alert_level"] if setup["direction"] == "LONG" else price >= setup["alert_level"]


def entry_reached(setup, price):
    return price <= setup["entry"] if setup["direction"] == "LONG" else price >= setup["entry"]


def main():
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("Проверь GitHub Secrets: TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID.")

    print("Запуск Fibonacci Telegram Scanner")
    active = load_json(ACTIVE_FILE, {})
    sent = load_sent()
    symbols = get_top_symbols()
    print(f"Сканируем {len(symbols)} пар на таймфрейме {TIMEFRAME}")

    # Сначала обновляем сохранённые импульсы.
    for symbol in list(active):
        try:
            setup = active[symbol]
            price = get_price(symbol)

            if stop_reached(setup, price):
                print(f"Сетап удалён: цена достигла стоп-уровня — {symbol} {setup['direction']}")
                del active[symbol]
                save_json(ACTIVE_FILE, active)
                continue

            if setup.get("status") == "waiting" and alert_reached(setup, price):
                signal_id = f"{symbol}:{setup['direction']}:{setup['candle_time']}:fib50"
                if signal_id not in sent:
                    message, keyboard = format_alert(setup, price, entry_reached(setup, price))
                    send_telegram(message, keyboard)
                    sent.add(signal_id)
                    save_sent(sent)
                    print(f"Отправлено уведомление 0.5: {symbol} {setup['direction']}")
                setup["status"] = "alerted"
                save_json(ACTIVE_FILE, active)
            else:
                print(f"Отслеживается {symbol}: цена {format_price(price)}; статус {setup.get('status')}")

        except Exception as error:
            print(f"Ошибка отслеживания {symbol}: {error}")

    # Новые импульсы ищем только там, где нет активного сетапа.
    for symbol in symbols:
        if symbol in active:
            continue
        try:
            setup = detect_impulse(symbol)
            if not setup:
                continue

            price = get_price(symbol)
            if stop_reached(setup, price):
                continue

            active[symbol] = setup
            save_json(ACTIVE_FILE, active)
            print(f"Найден импульс: {symbol} {setup['direction']} ({setup['length']} свечей)")

            # Если откат до 0.5 уже произошёл к моменту обнаружения, уведомляем сразу.
            if alert_reached(setup, price):
                signal_id = f"{symbol}:{setup['direction']}:{setup['candle_time']}:fib50"
                if signal_id not in sent:
                    message, keyboard = format_alert(setup, price, entry_reached(setup, price))
                    send_telegram(message, keyboard)
                    sent.add(signal_id)
                    save_sent(sent)
                    print(f"Отправлено уведомление 0.5: {symbol} {setup['direction']}")
                active[symbol]["status"] = "alerted"
                save_json(ACTIVE_FILE, active)

        except Exception as error:
            print(f"Ошибка для {symbol}: {error}")

    save_json(ACTIVE_FILE, active)
    print("Сканирование завершено.")


if __name__ == "__main__":
    main()

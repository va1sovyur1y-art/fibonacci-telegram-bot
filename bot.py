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

session = requests.Session()
session.headers.update({"User-Agent": "FibonacciTelegramScanner/1.0"})


def get_json(url, params=None):
    response = session.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def get_top_symbols():
    data = get_json(f"{BASE_URL}/api/v3/ticker/24hr")
    excluded = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
    symbols = []

    for item in data:
        symbol = item.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue
        if any(symbol.endswith(x) for x in excluded):
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

    setups.sort(key=lambda s: s["move_pct"], reverse=True)
    return [set

import os
import json
import time
import logging
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np

BASE_URL = "https://api.binance.com"
TIMEFRAME = "15m"
TOP_SYMBOLS_LIMIT = 20
ATR_PERIOD = 14
MIN_IMPULSE_ATR = 2.0
MIN_EFFICIENCY = 0.55
IMPULSE_CANDLES = [3, 4, 5]
ENTRY_LEVEL = 0.618
STOP_LEVEL = 0.786
SETUP_MAX_AGE_CANDLES = 16
STATE_FILE = "bot_state.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("fibonacci-bot")
session = requests.Session()
session.headers.update({"User-Agent": "FibonacciTelegramBot/1.0"})

EXCLUDED_BASES = {
    "USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE", "USDD",
    "BUSD", "UST", "USTC", "EUR", "EURC", "XAUT"
}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


def api_get(path, params=None):
    response = session.get(BASE_URL + path, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def get_top_symbols(limit=TOP_SYMBOLS_LIMIT):
    exchange_info = api_get("/api/v3/exchangeInfo")
    tickers = api_get("/api/v3/ticker/24hr")
    ticker_map = {item["symbol"]: item for item in tickers}
    eligible = []
    for item in exchange_info["symbols"]:
        if item.get("status") != "TRADING" or item.get("quoteAsset") != "USDT":
            continue
        if not item.get("isSpotTradingAllowed", False):
            continue
        symbol = item["symbol"]
        base = item.get("baseAsset", "")
        if base in EXCLUDED_BASES or any(base.endswith(s) for s in LEVERAGED_SUFFIXES):
            continue
        if symbol not in ticker_map:
            continue
        try:
            volume = float(ticker_map[symbol].get("quoteVolume", 0))
        except (TypeError, ValueError):
            continue
        eligible.append((symbol, volume))
    eligible.sort(key=lambda x: x[1], reverse=True)
    return [x[0] for x in eligible[:limit]]


def get_closed_candles(symbol, limit=100):
    raw = api_get("/api/v3/klines", {"symbol": symbol, "interval": TIMEFRAME, "limit": limit})
    now_ms = int(time.time() * 1000)
    raw = [r for r in raw if int(r[6]) < now_ms]
    columns = [
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"
    ]
    df = pd.DataFrame(raw, columns=columns)
    if df.empty:
        return df
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df.reset_index(drop=True)


def find_impulses(df):
    if len(df) < ATR_PERIOD + max(IMPULSE_CANDLES) + 2:
        return []
    data = df.copy().reset_index(drop=True)
    prev_close = data["close"].shift(1)
    tr = pd.concat([
        data["high"] - data["low"],
        (data["high"] - prev_close).abs(),
        (data["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    data["atr"] = tr.rolling(ATR_PERIOD).mean()
    found = []
    end = len(data) - 1
    for length in IMPULSE_CANDLES:
        start = end - length + 1
        window = data.iloc[start:end + 1]
        start_price = float(data.iloc[start]["open"])
        end_price = float(data.iloc[end]["close"])
        atr = float(data.iloc[start]["atr"])
        if not np.isfinite(atr) or atr <= 0 or start_price <= 0:
            continue
        move = end_price - start_price
        if move == 0:
            continue
        move_pct = move / start_price * 100
        move_atr = abs(move) / atr
        if move_atr < MIN_IMPULSE_ATR:
            continue
        path = float(window["close"].diff().abs().sum())
        efficiency = abs(move) / path if path > 0 else 0
        if efficiency < MIN_EFFICIENCY:
            continue
        found.append({
            "direction": "LONG" if move > 0 else "SHORT",
            "candles": length,
            "move_pct": move_pct,
            "move_atr": move_atr,
            "efficiency": efficiency,
            "start_time": data.iloc[start]["open_time"].isoformat(),
            "end_time": data.iloc[end]["open_time"].isoformat(),
            "start_price": start_price,
            "end_price": end_price,
            "impulse_low": float(window["low"].min()),
            "impulse_high": float(window["high"].max())
        })
    return found


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict):
            raise ValueError("bad state")
        state.setdefault("setups", {})
        state.setdefault("sent_alerts", [])
        return state
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return {"setups": {}, "sent_alerts": []}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def send_telegram(message):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        raise RuntimeError("В GitHub Secrets не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID.")
    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": message},
        timeout=20
    )
    response.raise_for_status()
    if not response.json().get("ok"):
        raise RuntimeError("Telegram API вернул ошибку.")


def fmt_price(value):
    value = float(value)
    if value >= 1000:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.5f}"
    if value >= 0.01:
        return f"{value:.6f}"
    return f"{value:.8f}"


def make_setup(symbol, impulse):
    low, high = float(impulse["impulse_low"]), float(impulse["impulse_high"])
    if high <= low:
        return None
    size = high - low
    direction = impulse["direction"]
    if direction == "LONG":
        entry = high - size * ENTRY_LEVEL
        stop = high - size * STOP_LEVEL
        if float(impulse["end_price"]) <= entry:
            return None
        risk = abs(entry - stop)
        tp3, tp4 = entry + risk * 3, entry + risk * 4
    else:
        entry = low + size * ENTRY_LEVEL
        stop = low + size * STOP_LEVEL
        if float(impulse["end_price"]) >= entry:
            return None
        risk = abs(stop - entry)
        tp3, tp4 = entry - risk * 3, entry - risk * 4
    return {
        "symbol": symbol, "direction": direction, "candles": int(impulse["candles"]),
        "move_pct": float(impulse["move_pct"]), "entry": float(entry), "stop": float(stop),
        "tp3": float(tp3), "tp4": float(tp4), "end_time": impulse["end_time"],
        "status": "WAITING", "last_checked_candle": impulse["end_time"]
    }


def setup_key(setup):
    return f"{setup['symbol']}|{setup['direction']}|{setup['end_time']}"


def setup_message(s):
    direction = "LONG 🟢" if s["direction"] == "LONG" else "SHORT 🔴"
    return (
        "📊 НОВЫЙ СЕТАП — ПРОВЕРЬ НА ГРАФИКЕ\n\n"
        f"Монета: {s['symbol']}\nНаправление: {direction}\n"
        f"Импульс: {s['move_pct']:.3f}% ({s['candles']} свечи)\n"
        f"Вход 0.618: {fmt_price(s['entry'])}\nСтоп 0.786: {fmt_price(s['stop'])}\n"
        f"TP 1:3: {fmt_price(s['tp3'])}\nTP 1:4: {fmt_price(s['tp4'])}\n\n"
        "Это кандидат на сетап, не подтверждённый вход."
    )


def bounce_message(s, candle_time, close):
    direction = "LONG 🟢" if s["direction"] == "LONG" else "SHORT 🔴"
    return (
        "🔔 ВОЗМОЖНЫЙ ОТБОЙ ОТ ФИБОНАЧЧИ\n\n"
        f"Монета: {s['symbol']}\nНаправление: {direction}\n"
        f"Вход 0.618: {fmt_price(s['entry'])}\nСтоп 0.786: {fmt_price(s['stop'])}\n"
        f"TP 1:3: {fmt_price(s['tp3'])}\nTP 1:4: {fmt_price(s['tp4'])}\n"
        f"Закрытие свечи: {fmt_price(close)}\nВремя UTC: {candle_time}\n\n"
        "Проверь график вручную. Прибыль не гарантируется."
    )


def run_once():
    state = load_state()
    symbols = get_top_symbols(TOP_SYMBOLS_LIMIT)
    log.info("Получен список из %d торговых пар.", len(symbols))
    new_setups = 0
    bounce_alerts = 0

    # Проверяем уже сохранённые сетапы только по свечам, закрывшимся после импульса.
    for key, s in list(state["setups"].items()):
        try:
            df = get_closed_candles(s["symbol"], limit=100)
            if df.empty:
                continue
            impulse_time = pd.to_datetime(s["end_time"], utc=True)
            age = int((df.iloc[-1]["open_time"] - impulse_time).total_seconds() / 900)
            if age > SETUP_MAX_AGE_CANDLES:
                del state["setups"][key]
                continue
            last_checked = pd.to_datetime(s["last_checked_candle"], utc=True)
            newer = df[(df["open_time"] > impulse_time) & (df["open_time"] > last_checked)]
            for _, candle in newer.iterrows():
                lo, hi, close = float(candle["low"]), float(candle["high"]), float(candle["close"])
                ctime = candle["open_time"].isoformat()
                if s["direction"] == "LONG":
                    if lo <= s["stop"]:
                        del state["setups"][key]
                        break
                    if lo <= s["entry"] and close > s["entry"]:
                        send_telegram(bounce_message(s, ctime, close))
                        del state["setups"][key]
                        bounce_alerts += 1
                        break
                else:
                    if hi >= s["stop"]:
                        del state["setups"][key]
                        break
                    if hi >= s["entry"] and close < s["entry"]:
                        send_telegram(bounce_message(s, ctime, close))
                        del state["setups"][key]
                        bounce_alerts += 1
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

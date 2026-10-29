import os
import time
import traceback

import bot


def main():
    if not bot.TOKEN or not bot.CHAT_ID:
        raise RuntimeError(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables."
        )

    print("Telegram interaction worker started; polling for messages and button presses.")
    while True:
        try:
            # Long polling handles calculator conversations independently of market scans.
            bot.process_telegram_updates()
        except Exception:
            traceback.print_exc()
            time.sleep(3)


if __name__ == "__main__":
    main()

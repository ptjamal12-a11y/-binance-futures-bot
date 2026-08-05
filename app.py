import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request

from scanner import FuturesScanner
from notifier import TelegramNotifier

app = Flask(__name__)

scanner = FuturesScanner(
    quote_asset=os.getenv("QUOTE_ASSET", "USDT"),
    interval=os.getenv("INTERVAL", "5m"),
    candle_limit=int(os.getenv("CANDLE_LIMIT", "220")),
    max_symbols=int(os.getenv("MAX_SYMBOLS", "80")),
    min_quote_volume=float(os.getenv("MIN_QUOTE_VOLUME", "25000000")),
)

notifier = TelegramNotifier(
    token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
    chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
)

SCAN_EVERY_SECONDS = max(60, int(os.getenv("SCAN_EVERY_SECONDS", "300")))
MIN_SCORE_TO_NOTIFY = int(os.getenv("MIN_SCORE_TO_NOTIFY", "75"))
AUTO_SCAN = os.getenv("AUTO_SCAN", "true").lower() == "true"

state = {
    "last_scan_at": None,
    "last_error": None,
    "signals": [],
    "running": False,
}


def run_scan(send_notifications: bool = True):
    if state["running"]:
        return {"status": "busy"}

    state["running"] = True
    try:
        signals = scanner.scan_market()
        state["signals"] = signals
        state["last_scan_at"] = datetime.now(timezone.utc).isoformat()
        state["last_error"] = None

        if send_notifications and notifier.enabled:
            for signal in signals:
                if signal["score"] >= MIN_SCORE_TO_NOTIFY:
                    notifier.send_signal(signal)

        return {
            "status": "ok",
            "count": len(signals),
            "signals": signals,
        }
    except Exception as exc:
        state["last_error"] = str(exc)
        return {"status": "error", "error": str(exc)}
    finally:
        state["running"] = False


def background_loop():
    time.sleep(8)
    while True:
        run_scan(send_notifications=True)
        time.sleep(SCAN_EVERY_SECONDS)


@app.get("/")
def home():
    return jsonify({
        "name": "Binance Futures Signal Scanner",
        "mode": "analysis-only",
        "auto_trading": False,
        "interval": scanner.interval,
        "last_scan_at": state["last_scan_at"],
        "last_error": state["last_error"],
        "endpoints": ["/health", "/scan", "/signals"],
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok", "running": state["running"]})


@app.route("/scan", methods=["GET", "POST"])
def scan():
    notify = request.args.get("notify", "false").lower() == "true"
    result = run_scan(send_notifications=notify)
    status_code = 200 if result.get("status") in {"ok", "busy"} else 500
    return jsonify(result), status_code


@app.get("/signals")
def signals():
    limit = min(max(int(request.args.get("limit", "20")), 1), 100)
    return jsonify({
        "last_scan_at": state["last_scan_at"],
        "signals": state["signals"][:limit],
    })


if AUTO_SCAN:
    threading.Thread(target=background_loop, daemon=True).start()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

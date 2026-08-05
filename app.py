import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request, render_template_string

from scanner import FuturesScanner
from notifier import TelegramNotifier
from store import SignalStore

app = Flask(__name__)

scanner = FuturesScanner(
    quote_asset=os.getenv("QUOTE_ASSET", "USDT"),
    interval=os.getenv("INTERVAL", "3m"),
    trend_interval=os.getenv("TREND_INTERVAL", "15m"),
    candle_limit=int(os.getenv("CANDLE_LIMIT", "220")),
    max_symbols=int(os.getenv("MAX_SYMBOLS", "60")),
    shortlist_size=int(os.getenv("SHORTLIST_SIZE", "20")),
    min_quote_volume=float(os.getenv("MIN_QUOTE_VOLUME", "20000000")),
    workers=int(os.getenv("WORKERS", "6")),
)

notifier = TelegramNotifier(
    token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
    chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
)

store = SignalStore(
    duplicate_cooldown_minutes=int(os.getenv("DUPLICATE_COOLDOWN_MINUTES", "45")),
    history_limit=int(os.getenv("HISTORY_LIMIT", "250")),
)

SCAN_EVERY_SECONDS = max(60, int(os.getenv("SCAN_EVERY_SECONDS", "60")))
MIN_SCORE_TO_NOTIFY = int(os.getenv("MIN_SCORE_TO_NOTIFY", "78"))
TOP_SIGNALS = int(os.getenv("TOP_SIGNALS", "3"))
AUTO_SCAN = os.getenv("AUTO_SCAN", "true").lower() == "true"

state = {
    "last_scan_at": None,
    "last_scan_seconds": None,
    "last_error": None,
    "signals": [],
    "market_count": 0,
    "running": False,
    "scan_number": 0,
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def run_scan(send_notifications: bool = True):
    if state["running"]:
        return {"status": "busy", "message": "يوجد فحص جارٍ الآن"}

    state["running"] = True
    started = time.monotonic()

    try:
        result = scanner.scan_market()
        signals = result["signals"][:TOP_SIGNALS]

        state["signals"] = signals
        state["market_count"] = result["market_count"]
        state["last_scan_at"] = utc_now()
        state["last_scan_seconds"] = round(time.monotonic() - started, 2)
        state["last_error"] = None
        state["scan_number"] += 1

        sent = []
        for signal in signals:
            store.add(signal)

            if (
                send_notifications
                and notifier.enabled
                and signal["score"] >= MIN_SCORE_TO_NOTIFY
                and store.should_notify(signal)
            ):
                if notifier.send_signal(signal):
                    store.mark_notified(signal)
                    sent.append(signal["symbol"])

        return {
            "status": "ok",
            "market_count": state["market_count"],
            "signal_count": len(signals),
            "notifications_sent": sent,
            "scan_seconds": state["last_scan_seconds"],
            "signals": signals,
        }

    except Exception as exc:
        state["last_error"] = f"{type(exc).__name__}: {exc}"
        state["last_scan_at"] = utc_now()
        return {"status": "error", "error": state["last_error"]}
    finally:
        state["running"] = False


def background_loop():
    time.sleep(5)
    while True:
        run_scan(send_notifications=True)
        time.sleep(SCAN_EVERY_SECONDS)


@app.get("/")
def home():
    return jsonify({
        "name": "Binance Futures Scalping Scanner Pro",
        "status": "running",
        "mode": "analysis-only",
        "auto_trading": False,
        "interval": scanner.interval,
        "trend_interval": scanner.trend_interval,
        "scan_every_seconds": SCAN_EVERY_SECONDS,
        "telegram_connected": notifier.enabled,
        "last_scan_at": state["last_scan_at"],
        "last_scan_seconds": state["last_scan_seconds"],
        "last_error": state["last_error"],
        "market_count": state["market_count"],
        "signal_count": len(state["signals"]),
        "endpoints": ["/dashboard", "/health", "/scan", "/signals", "/history"],
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "scanner_running": state["running"],
        "last_scan_at": state["last_scan_at"],
        "last_error": state["last_error"],
    })


@app.route("/scan", methods=["GET", "POST"])
def scan():
    notify = request.args.get("notify", "false").lower() == "true"
    result = run_scan(send_notifications=notify)
    return jsonify(result), 200 if result["status"] in {"ok", "busy"} else 500


@app.get("/signals")
def signals():
    return jsonify({
        "last_scan_at": state["last_scan_at"],
        "scan_seconds": state["last_scan_seconds"],
        "signals": state["signals"],
    })


@app.get("/history")
def history():
    try:
        limit = min(max(int(request.args.get("limit", "50")), 1), 250)
    except ValueError:
        limit = 50
    return jsonify({"history": store.history(limit)})


DASHBOARD = r"""
<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ماسح فرص العقود</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0b0f14;color:#eef2f7;margin:0;padding:18px}
h1{font-size:24px;margin:0 0 6px}.muted{color:#9ba7b4}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin:16px 0}
.card{background:#141b23;border:1px solid #25303c;border-radius:14px;padding:14px}.signal{margin:12px 0}.long{border-right:5px solid #2ecc71}.short{border-right:5px solid #ff4d67}
.row{display:flex;justify-content:space-between;gap:8px;margin:6px 0}.score{font-size:22px;font-weight:700}.btn{display:inline-block;background:#3478f6;color:#fff;padding:11px 16px;border-radius:10px;text-decoration:none;margin:8px 0}
small{color:#9ba7b4}ul{padding-right:20px}.error{color:#ff7b8d}
</style>
</head>
<body>
<h1>ماسح Binance Futures</h1>
<div class="muted">تحليل آلي فقط — لا يفتح صفقات</div>
<a class="btn" href="/scan">تشغيل فحص يدوي</a>
<div class="grid">
<div class="card"><small>آخر فحص</small><div>{{last_scan or "لم يبدأ"}}</div></div>
<div class="card"><small>الأسواق المفحوصة</small><div class="score">{{market_count}}</div></div>
<div class="card"><small>مدة الفحص</small><div class="score">{{scan_seconds or "-"}} ث</div></div>
<div class="card"><small>تيليجرام</small><div>{{"متصل" if telegram else "غير مربوط"}}</div></div>
</div>
{% if error %}<div class="card error">{{error}}</div>{% endif %}
{% for s in signals %}
<div class="card signal {{'long' if s.side == 'LONG' else 'short'}}">
<div class="row"><strong>{{s.symbol}}</strong><span class="score">{{s.score}}/100</span></div>
<div class="row"><span>{{"شراء LONG" if s.side == "LONG" else "بيع SHORT"}}</span><span>{{s.interval}}</span></div>
<div class="row"><span>الدخول المرجعي</span><strong>{{s.entry_reference}}</strong></div>
<div class="row"><span>وقف الخسارة</span><strong>{{s.stop_loss}}</strong></div>
<div class="row"><span>الهدف 1</span><strong>{{s.target_1}}</strong></div>
<div class="row"><span>الهدف 2</span><strong>{{s.target_2}}</strong></div>
<div class="row"><span>RSI</span><span>{{s.rsi}}</span></div>
<div class="row"><span>الفوليوم</span><span>×{{s.volume_ratio}}</span></div>
<div class="row"><span>Funding</span><span>{{s.funding_rate_percent}}%</span></div>
<div class="row"><span>تغير OI</span><span>{{s.open_interest_change_percent}}%</span></div>
<ul>{% for r in s.reasons %}<li>{{r}}</li>{% endfor %}</ul>
</div>
{% else %}
<div class="card">لا توجد إشارات بعد. افتح <b>/scan</b> أو انتظر الفحص التلقائي.</div>
{% endfor %}
</body>
</html>
"""


@app.get("/dashboard")
def dashboard():
    return render_template_string(
        DASHBOARD,
        signals=state["signals"],
        last_scan=state["last_scan_at"],
        market_count=state["market_count"],
        scan_seconds=state["last_scan_seconds"],
        telegram=notifier.enabled,
        error=state["last_error"],
    )


if AUTO_SCAN:
    threading.Thread(target=background_loop, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))

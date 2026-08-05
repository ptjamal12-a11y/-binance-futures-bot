import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string

from live_engine import LiveEngine
from notifier import TelegramNotifier

app = Flask(__name__)

notifier = TelegramNotifier(
    token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
    chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
)

engine = LiveEngine(
    notifier=notifier,
    min_score=int(os.getenv("MIN_SCORE", "72")),
    cooldown_minutes=int(os.getenv("DUPLICATE_COOLDOWN_MINUTES", "30")),
)

AUTO_START = os.getenv("AUTO_START", "true").lower() == "true"

if AUTO_START:
    engine.start()


@app.get("/")
def home():
    return jsonify(engine.status())


@app.get("/health")
def health():
    status = engine.status()
    return jsonify({
        "status": "ok",
        "websocket_connected": status["websocket_connected"],
        "last_message_at": status["last_message_at"],
        "last_error": status["last_error"],
    })


@app.get("/signals")
def signals():
    return jsonify({
        "signals": engine.get_signals(),
        "status": engine.status(),
    })


@app.get("/restart")
def restart():
    engine.restart()
    return jsonify({"status": "restarting"})


DASHBOARD = """
<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Binance Futures Live</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0b0f14;color:#eef2f7;margin:0;padding:16px}
h1{font-size:23px;margin:0 0 8px}.muted{color:#9ba7b4}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:15px 0}
.card{background:#141b23;border:1px solid #25303c;border-radius:14px;padding:13px;margin:10px 0}.long{border-right:5px solid #25c26e}.short{border-right:5px solid #ff5368}
.row{display:flex;justify-content:space-between;gap:10px;margin:6px 0}.big{font-size:21px;font-weight:700}.ok{color:#25c26e}.bad{color:#ff5368}
a{color:#75a7ff}.btn{display:inline-block;background:#3478f6;color:white;padding:10px 15px;border-radius:10px;text-decoration:none}
ul{padding-right:20px}
</style>
<meta http-equiv="refresh" content="20">
</head>
<body>
<h1>Binance Futures — بث مباشر</h1>
<div class="muted">WebSocket فقط — لا يستخدم REST ولا يفتح صفقات</div>
<div class="grid">
<div class="card"><small>الاتصال</small><div class="big {{'ok' if s.websocket_connected else 'bad'}}">{{"متصل" if s.websocket_connected else "غير متصل"}}</div></div>
<div class="card"><small>آخر رسالة</small><div>{{s.last_message_at or "-"}}</div></div>
<div class="card"><small>الرسائل</small><div class="big">{{s.message_count}}</div></div>
<div class="card"><small>العملات الجاهزة</small><div class="big">{{s.ready_symbols}} / {{s.symbol_count}}</div></div>
<div class="card"><small>تيليجرام</small><div>{{"متصل" if s.telegram_connected else "غير مربوط"}}</div></div>
</div>
<a class="btn" href="/restart">إعادة الاتصال</a>
{% if s.last_error %}<div class="card bad">{{s.last_error}}</div>{% endif %}
{% for x in signals %}
<div class="card {{'long' if x.side == 'LONG' else 'short'}}">
<div class="row"><strong>{{x.symbol}}</strong><span class="big">{{x.score}}/100</span></div>
<div class="row"><span>{{"شراء LONG" if x.side == "LONG" else "بيع SHORT"}}</span><span>{{x.created_at}}</span></div>
<div class="row"><span>الدخول</span><strong>{{x.entry}}</strong></div>
<div class="row"><span>الوقف</span><strong>{{x.stop}}</strong></div>
<div class="row"><span>الهدف 1</span><strong>{{x.target1}}</strong></div>
<div class="row"><span>الهدف 2</span><strong>{{x.target2}}</strong></div>
<div class="row"><span>RSI</span><span>{{x.rsi}}</span></div>
<div class="row"><span>الفوليوم</span><span>×{{x.volume_ratio}}</span></div>
<div class="row"><span>Funding</span><span>{{x.funding}}%</span></div>
<ul>{% for r in x.reasons %}<li>{{r}}</li>{% endfor %}</ul>
</div>
{% else %}
<div class="card">الاتصال يبدأ فورًا، والتحليل يبدأ بعد جمع 25 شمعة دقيقة تقريبًا. حالة الاتصال تظهر مباشرة.</div>
{% endfor %}
</body>
</html>
"""


@app.get("/dashboard")
def dashboard():
    return render_template_string(
        DASHBOARD,
        s=engine.status(),
        signals=engine.get_signals(),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
